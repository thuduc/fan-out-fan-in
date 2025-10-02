import json
import logging
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from lxml import etree

try:
    from .constants import TASK_UPDATES_STREAM
except ImportError:
    from constants import TASK_UPDATES_STREAM

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
    """
    Stateless worker that executes individual valuation tasks.
    Fetches task XML from cache, runs valuation script, stores results,
    and publishes completion events to task updates stream.
    """
    def __init__(self, redis_client, logger: Optional[logging.Logger] = None, vnas_script: Optional[str] = None):
        self.redis = redis_client
        self.logger = logger or LOGGER
        default_script = Path(__file__).resolve().parent / 'vnas.sh'
        self._vnas_script = Path(vnas_script).resolve() if vnas_script else default_script

    def handle_dispatch(self, entry: Dict[str, str]) -> Dict[str, str]:
        """
        Process task dispatch message from stream:task:dispatch.

        """
        context = self._parse_entry(entry)
        try:
            payload_xml = self.redis.get(context.payload_key)
            if payload_xml is None:
                raise FileNotFoundError(f"Missing task payload {context.payload_key}")
            result = self._execute_task(payload_xml)
            self.redis.set(context.result_key, result)
            self._publish_update(context, "completed")
            return {"status": "completed", "taskId": context.task_id}
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Task processing failed", extra={"taskId": context.task_id})
            failure = {"error": str(exc)}
            self._publish_update(context, "failed", str(exc))
            self._record_failure(context, failure)
            raise

    def _publish_update(self, context: TaskContext, status: str, stacktrace: str='') -> None:
        """Publish task completion/failure event to task updates stream.

        """
        event = {
            "requestId": context.request_id,
            "groupIdx": str(context.group_index),
            "groupName": context.group_name,
            "taskId": context.task_id,
            "valuationName": context.valuation_name,
            "resultKey": context.result_key,
            "status": status,
            "attempt": str(context.attempt),
            "result": stacktrace,
        }
        self.redis.xadd(TASK_UPDATES_STREAM, event)

    def _record_failure(self, context: TaskContext, failure: Dict[str, object]) -> None:
        """Record task failure details to Redis cache for debugging.

        """
        try:
            failure_key = f"cache:request:{context.request_id}:failure"
            enriched = {
                "taskId": context.task_id,
                "groupIdx": context.group_index,
                "attempt": context.attempt,
                **failure,
            }
            self.redis.set(failure_key, json.dumps(enriched))
        except Exception:  # noqa: BLE001
            self.logger.warning("Unable to persist failure detail", extra={"taskId": context.task_id})

    def _parse_entry(self, entry: Dict[str, str]) -> TaskContext:
        """Parse stream entry into TaskContext object.

        """
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
        """Execute valuation computation on task XML.

        Parses XML, generates valuation amount via external script, updates
        amount node, and returns serialized XML.

        """
        # raise NotImplementedError("TaskService.evaluate must be implemented by the integrator.")
        #print(f"Executing task with payload: {xml_payload}")
        if isinstance(xml_payload, str):
            xml_payload = xml_payload.encode("utf-8")
        valuation_element = etree.fromstring(xml_payload)
        amount_nodes = valuation_element.xpath(".//analytics/price/amount")
        if amount_nodes:
            amount_nodes[0].text = self._generate_amount()
        return etree.tostring(valuation_element, encoding="UTF-8").decode("UTF-8")

    def _generate_amount(self) -> str:
        """Generate valuation amount by invoking external script.

        """
        try:
            completed = subprocess.run(
                [str(self._vnas_script)],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:  # noqa: PERF203
            raise RuntimeError("Failed to invoke valuation number generator") from exc

        raw_value = completed.stdout.strip()
        try:
            amount = float(raw_value)
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise RuntimeError("Valuation number generator returned invalid output") from exc

        if amount <= 0:
            raise RuntimeError("Valuation number generator returned non-positive value")

        return f"{amount:.2f}"
