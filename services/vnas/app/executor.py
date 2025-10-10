"""Task execution strategies for valuation processing."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from lxml import etree


class TaskExecutor(Protocol):
    """Protocol for task execution implementations."""

    def execute(self, xml_payload: str) -> str:
        """Execute valuation task and return result XML string."""
        ...


class DefaultTaskExecutor:
    """Default executor that uses vnas.sh script for valuation."""

    def __init__(self, vnas_script: Path):
        """Initialize with path to vnas.sh script."""
        self._vnas_script = vnas_script

    def execute(self, xml_payload: str) -> str:
        """Execute valuation computation on task XML.

        Parses XML, generates valuation amount via external script,
        updates amount node, and returns serialized XML.
        """
        if isinstance(xml_payload, str):
            xml_payload = xml_payload.encode("utf-8")

        valuation_element = etree.fromstring(xml_payload)
        amount_nodes = valuation_element.xpath(".//analytics/price/amount")

        if amount_nodes:
            amount_nodes[0].text = self._generate_amount()

        return etree.tostring(valuation_element, encoding="UTF-8").decode("UTF-8")

    def _generate_amount(self) -> str:
        """Generate valuation amount by invoking external script."""
        try:
            completed = subprocess.run(
                [str(self._vnas_script)],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("Failed to invoke valuation number generator") from exc

        raw_value = completed.stdout.strip()
        try:
            amount = float(raw_value)
        except ValueError as exc:
            raise RuntimeError("Valuation number generator returned invalid output") from exc

        if amount <= 0:
            raise RuntimeError("Valuation number generator returned non-positive value")

        return f"{amount:.2f}"
