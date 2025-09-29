from __future__ import annotations

import os
import json
import logging
import time
import xml.etree.ElementTree as ET
from lxml import etree
import copy
import redis
from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from task_invoker import TaskInvoker
from hydration.engine import HydrationEngine, HydrationStrategy
from hydration.strategies import HrefHydrationStrategy, SelectHydrationStrategy, UseFunctionHydrationStrategy
from hydration.fetchers.s3 import S3ResourceFetcher

from .constants import (
    GROUP_STATE_KEY_TEMPLATE,
    REQUEST_LIFECYCLE_STREAM,
    REQUEST_STATE_KEY_TEMPLATE,
    TASK_RESULT_KEY_TEMPLATE,
    TASK_UPDATES_STREAM,
    TASK_XML_KEY_TEMPLATE,
    DEFAULT_BLOCK_MS,
    TASK_WAIT_TIMEOUT_MS,
    MAX_TASK_RETRIES,
)

LOGGER = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

def create_redis():
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)

def close_redis(conn: Optional[redis.Redis]) -> None:
    conn.close()

@dataclass
class TaskDescriptor:
    request_id: str
    group_index: int
    group_name: str
    task_id: str
    xml_key: str
    result_key: str


class RequestOrchestrator:
    def __init__(
        self,
        hydration_engine: Optional[HydrationEngine] = None,
        task_invoker: Optional[object] = None,
        logger: Optional[logging.Logger] = None,
    ):
        if hydration_engine is None:
            fetcher = S3ResourceFetcher()
            href_strategy = HrefHydrationStrategy(fetcher)
            engine = HydrationEngine(
                strategies=[
                    href_strategy,
                    UseFunctionHydrationStrategy(),
                    SelectHydrationStrategy(),
                    href_strategy,
                ]
            )
            hydration_engine = engine
        self._hydration_engine = hydration_engine
        self.logger = logger or LOGGER
        self.task_invoker = task_invoker
        self.redis = create_redis()
        self.redis_task_update_stream = create_redis()

    def run(self, event: Dict[str, str]) -> Dict[str, str]:
        request_id = event["requestId"]
        xml_key = event["xmlKey"]
        response_key = event.get("responseKey") or f"cache:request:{request_id}:response"

        raw_xml = self.redis.get(xml_key)
        if raw_xml is None:
            raise ValueError(f"Request XML not found for key {xml_key}")

        try:
            root = etree.fromstring(raw_xml.encode("UTF-8"))
        except etree.XMLSyntaxError as exc:
            raise ValueError("Input XML is not well-formed.") from exc

        hydrated_items = self._hydration_engine.hydrate_element(root, root)
        if hydrated_items:
            root = hydrated_items[0].element

        project = root.find("project")
        groups = project.findall("group") if project is not None else []
        group_count = project.xpath("count(./group)")

        self.logger.info("Processing request", extra={"requestId": request_id, "groups": group_count})
        self._ensure_updates_consumer_group(request_id)
        self._update_request_state(request_id, status="started", group_count=group_count)
        self._publish_lifecycle(request_id, "started", {"groupCount": group_count})

        try:
            for index, group in enumerate(groups):
                self._mark_request_state(request_id, currentGroup=index, status="running")
                self._publish_lifecycle(request_id, "group_started", {"group": index})
                task_descriptors = self._dispatch_group(request_id, index, group, root)
                self._await_group_completion(request_id, index, task_descriptors, group)
                self._publish_lifecycle(request_id, "group_completed", {"group": index})
                
        except Exception as exc:  # noqa: BLE001
            self._record_request_failure(request_id, {
                "error": str(exc),
                "stage": "group_processing",
            })
            raise

        response_xml = etree.tostring(root, pretty_print=True, encoding="unicode")
        self.redis.set(response_key, response_xml)
        self._mark_request_state(
            request_id,
            status="succeeded",
            response_key=response_key,
            completedAt=_now_iso(),
        )
        self._publish_lifecycle(request_id, "completed", {"responseKey": response_key})

        # close redis connections
        close_redis(self.redis)
        close_redis(self.redis_task_update_stream)

        return {"responseKey": response_key, "groupCount": group_count}

    # --- Dispatch helpers -----------------------------------------------------------------

    def _dispatch_group(self, request_id: str, group_index: int, group: etree._Element, root: etree._Element) -> List[TaskDescriptor]:
        hydrated_groups = self._hydration_engine.hydrate_element(group, root)
        if hydrated_groups:
            group = hydrated_groups[0].element

        # valuations = group.findall("valuation")
        #print (f"Dispatching hydrated_groups {group_index} with {len(valuations)} valuations")

        # hydrate each valuation in group, since each could result in multiple valuations
        valuations = []
        for val in group.findall("valuation"):
            hydrated_valuations = self._hydration_engine.hydrate_element(val, root)
            if hydrated_valuations:
                valuations.extend(item.element for item in hydrated_valuations)

        expected = len(valuations)
        group_key = GROUP_STATE_KEY_TEMPLATE.format(request_id=request_id, group_index=group_index)
        self.redis.hset(group_key, mapping={
            "expected": expected,
            "completed": 0,
            "failed": 0,
            "status": "running",
        })

        # create a template XML for task requests
        group_task_req_template = self.copy_without_nodes(
            root,
            [
                "/vnml/project/market",
                "/vnml/project/model",
                "/vnml/project/calculator",
                "/vnml/project/portfolio",
                "/vnml/project/group",
            ],
        )
        # add current group only
        group_task_req_template.find("project").append(copy.deepcopy(group))
        # remove all existing valuation nodes
        for node in group_task_req_template.xpath("/vnml/project/group/valuation", namespaces=None):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

        valuation_index = 0
        descriptors: List[TaskDescriptor] = []
        for valuation in valuations:
            # create a new task request XML from template
            group_task_req = copy.deepcopy(group_task_req_template)
            valuation_index += 1
            task_id = str(valuation_index)
            # create task_xml by using group_task_req to append valuation node to ./project/group
            group_task_req.find("project").find("group").append(copy.deepcopy(valuation))
            task_xml = etree.tostring(group_task_req, pretty_print=True, encoding="unicode")
            xml_key = TASK_XML_KEY_TEMPLATE.format(request_id=request_id, group_index=group_index, task_id=task_id)
            result_key = TASK_RESULT_KEY_TEMPLATE.format(request_id=request_id, group_index=group_index, task_id=task_id)
            self.redis.set(xml_key, task_xml)
            dispatch_payload = {
                "requestId": request_id,
                "groupIdx": str(group_index),
                "groupName": group.get("name", f"group-{group_index}"),
                "taskId": task_id,
                "payloadKey": xml_key,
                "resultKey": result_key,
                "attempt": "1",
            }
            self._invoke_task_processor(dispatch_payload)
            descriptors.append(TaskDescriptor(
                request_id=request_id,
                group_index=group_index,
                group_name=group.get("name", f"group-{group_index}"),
                task_id=task_id,
                xml_key=xml_key,
                result_key=result_key,
            ))

        return descriptors

    def copy_without_nodes(self, tree: etree._ElementTree, xpaths, ns=None) -> etree._ElementTree:
        """
        Return a deep-copied ElementTree with all nodes matching any of the
        provided XPath expressions removed.

        Args:
            tree   : lxml ElementTree parsed from your XML
            xpaths : iterable of XPath strings (e.g., ["//debug", "//price/amount"])
            ns     : optional namespace map, e.g. {"v": "http://example.com/v1"}
        """
        root_copy = copy.deepcopy(tree)
        # Remove matches from the copy
        for xp in xpaths:
            for node in root_copy.xpath(xp, namespaces=ns):
                # Only element/comment/PI nodes have parents you can remove from
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
                else:
                    # If the XPath hit the root, you can replace it or clear it
                    node.clear()  # minimal fallback
        return root_copy
    
    # --- Completion handling ---------------------------------------------------------------

    def _await_group_completion(self, request_id: str, group_index: int, descriptors: List[TaskDescriptor], group: etree._Element) -> None:
        expected = len(descriptors)
        consumer_group = self._consumer_group_name(request_id)
        consumer = f"orchestrator-{int(time.time() * 1000) % 10000}"
        deadline = time.time() + (TASK_WAIT_TIMEOUT_MS / 1000)
        completed = 0
        pending_failures: List[Dict[str, str]] = []

        descriptor_by_task = {desc.task_id: desc for desc in descriptors}

        # remove all valuation nodes from group (they will be re-added as results come in)
        for node in group.xpath("./valuation"):
            parent = node.getparent()
            parent.remove(node)

        while completed < expected:
            if time.time() > deadline:
                raise TimeoutError(f"Timed out waiting for group {group_index} completion")

            entries = self.redis_task_update_stream.xreadgroup(
                groupname=consumer_group,
                consumername=consumer,
                streams={TASK_UPDATES_STREAM: '>'},
                count=expected,
                block=DEFAULT_BLOCK_MS,
            )
            if not entries:
                continue

            for stream_name, messages in entries:
                #print(f"Received {len(messages)} messages from stream {stream_name}")
                if stream_name != TASK_UPDATES_STREAM:
                    continue
                for message_id, raw_values in messages:
                    #print(f"Processing message {message_id} with values {raw_values}")
                    if isinstance(raw_values, dict):
                        values = raw_values
                    else:
                        values = {}
                        for idx in range(0, len(raw_values), 2):
                            field = raw_values[idx]
                            value = raw_values[idx + 1] if idx + 1 < len(raw_values) else None
                            values[field] = value

                    entry_request_id = values.get("requestId")
                    if entry_request_id != request_id:
                        self.redis.xack(TASK_UPDATES_STREAM, consumer_group, message_id)
                        continue
                    entry_group = int(values.get("groupIdx", "-1"))
                    if entry_group != group_index:
                        # Another group's event; leave pending for the owning orchestrator instance
                        continue

                    status = values.get("status")
                    task_id = values.get("taskId")
                    descriptor = descriptor_by_task.get(task_id)
                    if descriptor is None:
                        self.redis.xack(TASK_UPDATES_STREAM, consumer_group, message_id)
                        continue

                    if status == "completed":
                        completed += 1
                        xml_payload = self._extract_result_payload(values)
                        # append valuation result to group
                        task_result_root = etree.fromstring(xml_payload.encode("UTF-8"))
                        valudation_result = task_result_root.find("project/group/valuation")
                        group.append(copy.deepcopy(valudation_result))

                        self.redis.hset(
                            GROUP_STATE_KEY_TEMPLATE.format(request_id=request_id, group_index=group_index),
                            mapping={"completed": completed}
                        )
                    elif status == "failed":
                        attempt_value = int(values.get("attempt", values.get("attempts", "1")))
                        if attempt_value < MAX_TASK_RETRIES:
                            self._enqueue_retry(values, attempt_value + 1)
                        else:
                            pending_failures.append(values)
                            self.redis.hset(
                                GROUP_STATE_KEY_TEMPLATE.format(request_id=request_id, group_index=group_index),
                                mapping={"failed": len(pending_failures)}
                            )
                    self.redis.xack(TASK_UPDATES_STREAM, consumer_group, message_id)

            if pending_failures:
                self._record_request_failure(request_id, {
                    "group": group_index,
                    "failures": pending_failures,
                })
                raise RuntimeError(f"Group {group_index} failed: {pending_failures}")

        self.redis.hset(
            GROUP_STATE_KEY_TEMPLATE.format(request_id=request_id, group_index=group_index),
            mapping={"status": "completed"}
        )

    def _extract_result_payload(self, values: Dict[str, str]) -> str:
        result_key = values.get("resultKey")
        result_payload = self.redis.get(result_key) if result_key else None
        return result_payload

    def _invoke_task_processor(self, payload: Dict[str, str]) -> None:
        if not self.task_invoker:
            raise RuntimeError("Task invoker is not configured")
        try:
            self.task_invoker.invoke_async(payload)
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Failed to invoke task processor", extra={"payload": payload, "error": str(exc)})
            raise

    def _enqueue_retry(self, values: Dict[str, str], attempt: int) -> None:
        retry_payload = dict(values)
        retry_payload["attempt"] = str(attempt)
        self._invoke_task_processor(retry_payload)

    def _mark_request_state(self, request_id: str, **fields: object) -> None:
        if not fields:
            return
        key = REQUEST_STATE_KEY_TEMPLATE.format(request_id=request_id)
        serialised = {}
        for name, value in fields.items():
            if isinstance(value, (dict, list)):
                serialised[name] = json.dumps(value)
            else:
                serialised[name] = value
        self.redis.hset(key, mapping=serialised)

    def _record_request_failure(self, request_id: str, detail: Dict[str, object]) -> None:
        failure_key = f"cache:request:{request_id}:failure"
        try:
            self.redis.set(failure_key, json.dumps(detail))
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Unable to persist failure detail", extra={"error": str(exc), "detail": detail})
        self._mark_request_state(request_id, status="failed", failureAt=_now_iso())
        self._publish_lifecycle(request_id, "failed", {"detail": json.dumps(detail)})

    # --- Lifecycle & state ---------------------------------------------------------------

    def _ensure_updates_consumer_group(self, request_id: str) -> None:
        group = self._consumer_group_name(request_id)
        if not hasattr(self.redis, "xgroup_create"):
            try:
                self.redis.xgroupCreate(TASK_UPDATES_STREAM, group, "$", {"MKSTREAM": True})
            except Exception as exc:  # noqa: BLE001
                if "BUSYGROUP" not in str(exc):
                    raise
        else:
            try:
                self.redis.xgroup_create(TASK_UPDATES_STREAM, group, "$", mkstream=True)
            except Exception as exc:  # noqa: BLE001
                if "BUSYGROUP" not in str(exc):
                    raise

    def _consumer_group_name(self, request_id: str) -> str:
        return f"req::{request_id}"

    def _publish_lifecycle(self, request_id: str, status: str, extra: Optional[Dict[str, object]] = None) -> None:
        payload = {"requestId": request_id, "status": status, "timestamp": time.time()}
        if extra:
            for key, value in extra.items():
                payload[key] = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        self.redis.xadd(REQUEST_LIFECYCLE_STREAM, payload)

    def _update_request_state(self, request_id: str, **fields: object) -> None:
        key = REQUEST_STATE_KEY_TEMPLATE.format(request_id=request_id)
        serialized = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in fields.items()}
        self.redis.hset(key, mapping=serialized)

    def _build_response_xml(self, request_id: str, grouped_results: Dict[int, List[Dict[str, str]]]) -> str:
        root = ET.Element("response", attrib={"requestId": request_id})
        for group_index, tasks in grouped_results.items():
            group_node = ET.SubElement(root, "group", attrib={"index": str(group_index)})
            for task in tasks:
                task_node = ET.SubElement(group_node, "task", attrib={"id": task["taskId"]})
                for key, value in task.items():
                    if key in {"taskId"}:
                        continue
                    child = ET.SubElement(task_node, key)
                    child.text = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        return ET.tostring(root, encoding="unicode")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def lambda_handler(event: Dict[str, str], context: object):
    try:
        import boto3
        function_name = os.environ.get("VN_VNAS_SERBIVE", "glv-vnas-service")
        lambda_client = boto3.client("lambda")
        task_invoker = TaskInvoker(
            client=lambda_client,
            function_name=function_name,
        )
        orchestrator = RequestOrchestrator(task_invoker=task_invoker)
        return orchestrator.run(event)
    except Exception as exc:
        LOGGER.exception("Orchestration failed", extra={"event": event, "error": str(exc)})
        raise