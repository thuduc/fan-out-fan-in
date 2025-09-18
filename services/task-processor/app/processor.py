import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Optional

from .constants import TASK_UPDATES_STREAM

LOGGER = logging.getLogger(__name__)


@dataclass
class TaskContext:
    request_id: str
    group_index: int
    group_name: str
    task_id: str
    valuation_name: str
    payload_key: str
    result_key: str
    attempt: int


class TaskProcessor:
    def __init__(self, redis_client, logger: Optional[logging.Logger] = None):
        self.redis = redis_client
        self.logger = logger or LOGGER

    def handle_dispatch(self, entry: Dict[str, str]) -> Dict[str, str]:
        context = self._parse_entry(entry)
        try:
            payload_xml = self.redis.get(context.payload_key)
            if payload_xml is None:
                raise FileNotFoundError(f"Missing task payload {context.payload_key}")
            result = self._execute_task(payload_xml)
            self.redis.set(context.result_key, json.dumps(result))
            self._publish_update(context, "completed", result)
            return {"status": "completed", "taskId": context.task_id, "result": result}
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Task processing failed", extra={"taskId": context.task_id})
            failure = {"error": str(exc)}
            self._publish_update(context, "failed", failure)
            raise

    def _publish_update(self, context: TaskContext, status: str, payload: Dict[str, object]) -> None:
        event = {
            "requestId": context.request_id,
            "groupIdx": str(context.group_index),
            "groupName": context.group_name,
            "taskId": context.task_id,
            "valuationName": context.valuation_name,
            "resultKey": context.result_key,
            "status": status,
            "attempt": str(context.attempt),
            "result": json.dumps(payload),
        }
        self.redis.xadd(TASK_UPDATES_STREAM, event)

    def _parse_entry(self, entry: Dict[str, str]) -> TaskContext:
        values = entry.get("values", entry)
        try:
            attempt = int(values.get("attempt", "1"))
        except ValueError as exc:
            raise ValueError("Invalid attempt value") from exc
        return TaskContext(
            request_id=values["requestId"],
            group_index=int(values["groupIdx"]),
            group_name=values.get("groupName", f"group-{values['groupIdx']}") if "groupIdx" in values else "",
            task_id=values["taskId"],
            valuation_name=values.get("valuationName", values["taskId"]),
            payload_key=values["payloadKey"],
            result_key=values["resultKey"],
            attempt=attempt,
        )

    def _execute_task(self, xml_payload: str) -> Dict[str, object]:
        root = ET.fromstring(xml_payload)
        valuation = root.find("valuation")
        if valuation is None:
            raise ValueError("Invalid task XML: missing valuation")
        valuation_name = valuation.get("name", "unknown")
        instrument = valuation.find("instrument")
        instrument_name = instrument.get("ref-name") if instrument is not None else "unknown"

        metrics = {}
        for amount_node in root.findall(".//amount"):
            metric_name = amount_node.tag
            try:
                metrics.setdefault(metric_name, 0.0)
                metrics[metric_name] += float(amount_node.text or "0")
            except ValueError:
                continue

        result = {
            "valuation": valuation_name,
            "instrument": instrument_name,
            "metrics": metrics,
        }

        prior_results_node = root.find("priorResults")
        if prior_results_node is not None:
            prior = {}
            for child in prior_results_node.findall("result"):
                prior[child.get("taskId", "unknown")] = child.text
            if prior:
                result["prior"] = prior
        return result
