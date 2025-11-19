from __future__ import annotations

import re
from typing import Dict
from lxml import etree

VAR_PATTERN = re.compile(
    r"\$(?P<name>[A-Za-z_][\w\.-]*)\s*=\s*(?P<value>\"[^\"]*\"|'[^']*'|[^\s]+)",
    flags=re.IGNORECASE,
)
TOKEN_PATTERN = re.compile(r"\$(?P<name>[A-Za-z_][\w\.-]*)")


def collect_pi_variables(root: etree._Element) -> Dict[str, str]:
    """
    Scan the XML document (prolog, subtree, epilog) for <?vnvs ...?>
    processing instructions and extract variables.
    Later declarations override earlier ones.
    """
    variables: Dict[str, str] = {}

    def parse_content(content: str) -> None:
        if not content:
            return
        for var_match in VAR_PATTERN.finditer(content):
            name = var_match.group("name")
            raw_value = var_match.group("value") or ""
            value = raw_value
            if (raw_value.startswith('"') and raw_value.endswith('"')) or (
                raw_value.startswith("'") and raw_value.endswith("'")
            ):
                value = raw_value[1:-1]
            variables[name] = value

    # 1. Prolog (preceding siblings of root)
    # itersiblings(preceding=True) yields siblings in reverse document order (closest first)
    # We reverse to process in document order
    prolog = [
        node
        for node in root.itersiblings(preceding=True)
        if isinstance(node, etree._ProcessingInstruction) and node.target == "vnvs"
    ]
    for node in reversed(prolog):
        parse_content(node.text)

    # 2. Subtree (inside root)
    for node in root.iter(etree.ProcessingInstruction):
        if node.target == "vnvs":
            parse_content(node.text)

    # 3. Epilog (following siblings of root)
    # itersiblings() yields siblings in document order
    for node in root.itersiblings():
        if isinstance(node, etree._ProcessingInstruction) and node.target == "vnvs":
            parse_content(node.text)

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
