from __future__ import annotations

import copy
from typing import Dict

from lxml import etree

from exceptions import HydrationError


class XmlHydrator:
    """Hydrates valuation elements by resolving select-based references."""

    def __init__(self) -> None:
        self._reference_cache: Dict[str, etree._Element] = {}

    def hydrate(self, valuation_element: etree._Element, document_root: etree._Element) -> etree._Element:
        hydrated = copy.deepcopy(valuation_element)
        nodes_with_select = hydrated.xpath(".//*[@select]")

        for node in nodes_with_select:
            select_expr = node.get("select")
            if not select_expr:
                raise HydrationError("Encountered select attribute without a value during hydration.")

            replacement_source = self._resolve_reference(select_expr, document_root)
            replacement_copy = copy.deepcopy(replacement_source)

            parent = node.getparent()
            if parent is None:
                raise HydrationError(
                    f"Cannot hydrate element <{node.tag}> without a parent; select expression '{select_expr}' is invalid."
                )

            insertion_index = parent.index(node)
            node.attrib.pop("select", None)
            parent.insert(insertion_index, replacement_copy)
            parent.remove(node)

        return hydrated

    def _resolve_reference(self, select_expr: str, document_root: etree._Element) -> etree._Element:
        if select_expr not in self._reference_cache:
            matches = document_root.xpath(select_expr)
            if len(matches) != 1:
                raise HydrationError(
                    f"Select expression '{select_expr}' resolved to {len(matches)} nodes; expected exactly one."
                )
            match = matches[0]
            if not isinstance(match, etree._Element):
                raise HydrationError(
                    f"Select expression '{select_expr}' does not reference an XML element."
                )
            self._reference_cache[select_expr] = match

        return self._reference_cache[select_expr]
