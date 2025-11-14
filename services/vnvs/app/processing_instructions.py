from __future__ import annotations

import re
from typing import Dict
from lxml import etree

PI_BLOCK_PATTERN = re.compile(r"<\?vnvs(?P<body>.*?)\?>", flags=re.IGNORECASE | re.DOTALL)
VAR_PATTERN = re.compile(
    r"\$(?P<name>[A-Za-z_][\w\.-]*)\s*=\s*(?P<value>\"[^\"]*\"|'[^']*'|[^\s]+)",
    flags=re.IGNORECASE,
)
TOKEN_PATTERN = re.compile(r"\$(?P<name>[A-Za-z_][\w\.-]*)")


def parse_processing_instructions(xml_text: str) -> Dict[str, str]:
    """
    Scan raw XML text for <?vnvs ...?> processing instructions and extract
    $var = value assignments into a mapping. Later declarations override
    earlier ones, matching common PI behavior.
    """
    if not xml_text or "<?vnvs" not in xml_text:
        return {}

    variables: Dict[str, str] = {}
    for block_match in PI_BLOCK_PATTERN.finditer(xml_text):
        body = block_match.group("body") or ""
        for var_match in VAR_PATTERN.finditer(body):
            name = var_match.group("name")
            raw_value = var_match.group("value") or ""
            value = raw_value
            if (raw_value.startswith('"') and raw_value.endswith('"')) or (
                raw_value.startswith("'") and raw_value.endswith("'")
            ):
                value = raw_value[1:-1]
            variables[name] = value
    return variables


def apply_pi_variables(root: etree._Element, variables: Dict[str, str]) -> None:
    """
    Replace $VAR tokens across element text, tail text, and attribute values
    using the provided PI mapping.
    """
    if not variables or root is None:
        return

    def replace_tokens(value: str) -> str:
        if not value or "$" not in value:
            return value

        def _replace(match: re.Match[str]) -> str:
            name = match.group("name")
            return variables.get(name, match.group(0))

        return TOKEN_PATTERN.sub(_replace, value)

    for element in root.iter():
        if element.text:
            element.text = replace_tokens(element.text)
        if element.tail:
            element.tail = replace_tokens(element.tail)
        for attr, attr_value in list(element.attrib.items()):
            element.attrib[attr] = replace_tokens(attr_value)
