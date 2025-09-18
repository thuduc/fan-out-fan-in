from __future__ import annotations

import json
import logging
import time
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

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


@dataclass
class TaskDescriptor:
    request_id: str
    group_index: int
    group_name: str
    task_id: str
    valuation_name: str
    xml_key: str
    result_key: str


class RequestOrchestrator:
    def __init__(
        self,
        redis_client,
        logger: Optional[logging.Logger] = None,
        task_invoker: Optional[object] = None,
    ):
        self.redis = redis_client
        self.logger = logger or LOGGER
        self.task_invoker = task_invoker

    def run(self, event: Dict[str, str]) -> Dict[str, str]:
        request_id = event["requestId"]
        xml_key = event["xmlKey"]
        response_key = event.get("responseKey") or f"cache:request:{request_id}:response"
        metadata_key = event.get("metadataKey")

        raw_xml = self.redis.get(xml_key)
        if raw_xml is None:
            raise ValueError(f"Request XML not found for key {xml_key}")

        project = self._parse_project(raw_xml)
        groups = project.get("groups", [])
        group_count = len(groups)

        self.logger.info("Processing request", extra={"requestId": request_id, "groups": group_count})
        self._ensure_updates_consumer_group(request_id)
        self._update_request_state(request_id, status="started", group_count=group_count)
        self._publish_lifecycle(request_id, "started", {"groupCount": group_count})

        aggregated_results: Dict[int, List[Dict[str, str]]] = {}
        prior_results: Dict[str, Dict[str, str]] = {}

        try:
            for index, group in enumerate(groups):
                aggregated_results[index] = []
                self._mark_request_state(request_id, currentGroup=index, status="running")
                self._publish_lifecycle(request_id, "group_started", {"group": index})
                task_descriptors = self._dispatch_group(request_id, index, group, project, prior_results)
                group_results = self._await_group_completion(request_id, index, task_descriptors)
                aggregated_results[index] = group_results
                prior_results.update({task["taskId"]: task for task in group_results})
                self._publish_lifecycle(request_id, "group_completed", {"group": index})
        except Exception as exc:  # noqa: BLE001
            self._record_request_failure(request_id, {
                "error": str(exc),
                "stage": "group_processing",
            })
            raise

        response_xml = self._build_response_xml(request_id, aggregated_results)
        self.redis.set(response_key, response_xml)
        self._mark_request_state(
            request_id,
            status="succeeded",
            response_key=response_key,
            completedAt=_now_iso(),
        )
        self._publish_lifecycle(request_id, "completed", {"responseKey": response_key})
        return {"responseKey": response_key, "groupCount": group_count}

    # --- Parsing helpers ------------------------------------------------------------------

    def _parse_project(self, xml: str) -> Dict[str, object]:
        tree = ET.fromstring(xml)
        project_element = tree.find("project")
        if project_element is None:
            raise ValueError("Invalid XML: missing <project> root")

        groups: List[Dict[str, object]] = []
        for group_index, group_element in enumerate(project_element.findall("group")):
            group_name = group_element.get("name") or f"Group{group_index + 1}"
            valuations = []
            for val_index, valuation in enumerate(group_element.findall("valuation")):
                val_name = valuation.get("name") or f"valuation-{val_index + 1}"
                task_id = f"g{group_index + 1}-t{val_index + 1}-{val_name}"
                valuations.append({
                    "taskId": task_id,
                    "valuationName": val_name,
                    "element": deepcopy(valuation),
                })
            groups.append({"name": group_name, "valuations": valuations})
        metadata = {
            "markets": [deepcopy(node) for node in project_element.findall("market")],
            "models": [deepcopy(node) for node in project_element.findall("model")],
            "calculators": [deepcopy(node) for node in project_element.findall("calculator")],
            "portfolio": deepcopy(project_element.find("portfolio")),
        }
        return {"metadata": metadata, "groups": groups}

    # --- Dispatch helpers -----------------------------------------------------------------

    def _dispatch_group(self, request_id: str, group_index: int, group: Dict[str, object], project: Dict[str, object], prior_results: Dict[str, Dict[str, str]]) -> List[TaskDescriptor]:
        valuations = group.get("valuations", [])
        expected = len(valuations)
        group_key = GROUP_STATE_KEY_TEMPLATE.format(request_id=request_id, group_index=group_index)
        self.redis.hset(group_key, mapping={
            "expected": expected,
            "completed": 0,
            "failed": 0,
            "status": "running",
        })

        descriptors: List[TaskDescriptor] = []
        for valuation in valuations:
            task_id = valuation["taskId"]
            valuation_name = valuation["valuationName"]
            task_xml = self._compose_task_xml(project["metadata"], valuation["element"], prior_results)
            xml_key = TASK_XML_KEY_TEMPLATE.format(request_id=request_id, group_index=group_index, task_id=task_id)
            result_key = TASK_RESULT_KEY_TEMPLATE.format(request_id=request_id, group_index=group_index, task_id=task_id)
            self.redis.set(xml_key, task_xml)
            dispatch_payload = {
                "requestId": request_id,
                "groupIdx": str(group_index),
                "groupName": group.get("name", f"group-{group_index}"),
                "taskId": task_id,
                "valuationName": valuation_name,
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
                valuation_name=valuation_name,
                xml_key=xml_key,
                result_key=result_key,
            ))
        return descriptors

    def _compose_task_xml(self, metadata: Dict[str, Iterable[ET.Element]], valuation_element: ET.Element, prior_results: Dict[str, Dict[str, str]]) -> str:
        task_root = ET.Element("taskRequest")
        header = ET.SubElement(task_root, "context")
        for market in metadata.get("markets", []):
            header.append(deepcopy(market))
        for model in metadata.get("models", []):
            header.append(deepcopy(model))
        for calculator in metadata.get("calculators", []):
            header.append(deepcopy(calculator))
        portfolio = metadata.get("portfolio")
        if portfolio is not None:
            header.append(deepcopy(portfolio))
        if prior_results:
            prior_container = ET.SubElement(task_root, "priorResults")
            for task_id, result in prior_results.items():
                result_node = ET.SubElement(prior_container, "result", attrib={"taskId": task_id})
                result_node.text = json.dumps(result)
        valuation_copy = deepcopy(valuation_element)
        task_root.append(valuation_copy)
        return ET.tostring(task_root, encoding="unicode")

    # --- Completion handling ---------------------------------------------------------------

    def _await_group_completion(self, request_id: str, group_index: int, descriptors: List[TaskDescriptor]) -> List[Dict[str, str]]:
        expected = len(descriptors)
        consumer_group = self._consumer_group_name(request_id)
        consumer = f"orchestrator-{int(time.time() * 1000) % 10000}"
        deadline = time.time() + (TASK_WAIT_TIMEOUT_MS / 1000)
        completed: List[Dict[str, str]] = []
        pending_failures: List[Dict[str, str]] = []

        descriptor_by_task = {desc.task_id: desc for desc in descriptors}

        while len(completed) < expected:
            if time.time() > deadline:
                raise TimeoutError(f"Timed out waiting for group {group_index} completion")

            entries = self.redis.xreadgroup(
                groupname=consumer_group,
                consumername=consumer,
                streams={TASK_UPDATES_STREAM: '>'},
                count=expected,
                block=DEFAULT_BLOCK_MS,
            )
            if not entries:
                continue

            for stream_name, messages in entries:
                if stream_name != TASK_UPDATES_STREAM:
                    continue
                for message_id, raw_values in messages:
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
                        result_payload = self._extract_result_payload(values)
                        completed.append({
                            "taskId": task_id,
                            "resultKey": descriptor.result_key,
                            "result": result_payload,
                        })
                        self.redis.hset(
                            GROUP_STATE_KEY_TEMPLATE.format(request_id=request_id, group_index=group_index),
                            mapping={"completed": len(completed)}
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
        return completed

    def _extract_result_payload(self, values: Dict[str, str]) -> Dict[str, str]:
        result_key = values.get("resultKey")
        payload = {
            "status": values.get("status"),
            "resultKey": result_key,
        }
        if "result" in values:
            try:
                payload.update(json.loads(values["result"]))
            except json.JSONDecodeError:
                payload["raw"] = values["result"]
        stored_result = self.redis.get(result_key) if result_key else None
        if stored_result:
            try:
                payload["stored"] = json.loads(stored_result)
            except json.JSONDecodeError:
                payload["stored"] = stored_result
        return payload

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
