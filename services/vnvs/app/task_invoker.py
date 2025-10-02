"""Wrapper for invoking the task processor Lambda."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional


class TaskInvoker:
    """
    Invokes vnas Lambda asynchronously for task processing.
    Lightweight adapter to enable test injection.
    """

    def __init__(
        self,
        client: Any,
        function_name: str,
        *,
        invocation_type: str = "Event",
        qualifier: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if client is None:
            raise ValueError("Lambda client is required")
        if not function_name:
            raise ValueError("function_name is required")
        self._client = client
        self._function_name = function_name
        self._invocation_type = invocation_type
        self._qualifier = qualifier
        self._logger = logger or logging.getLogger(__name__)

    def invoke_async(self, payload: dict) -> None:
        """Invoke the configured Lambda asynchronously.

        Raises a RuntimeError if the Lambda client reports a non-success status
        code. The payload is JSON-encoded before being sent.
        """

        invocation = {
            "FunctionName": self._function_name,
            "InvocationType": self._invocation_type,
            "Payload": json.dumps(payload).encode("utf-8"),
        }
        if self._qualifier:
            invocation["Qualifier"] = self._qualifier

        response = self._client.invoke(**invocation)
        status_code = response.get("StatusCode")
        if status_code is not None and status_code >= 300:
            self._logger.error(
                "Task Lambda invocation failed",
                extra={"statusCode": status_code, "payload": payload},
            )
            raise RuntimeError(f"Lambda invocation failed with status {status_code}")

        self._logger.debug(
            "Invoked task processor",
            extra={"functionName": self._function_name, "payload": payload},
        )

