from __future__ import annotations

import copy
from collections import deque
from typing import Deque, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..exceptions import HydrationError
from .engine import HydrationItem, HydrationStrategy, HydrationEngine
from .fetchers import (
    CompositeResourceFetcher,
    FileResourceFetcher,
    ResourceFetchError,
    ResourceFetcher,
    S3ResourceFetcher,
)


class HrefHydrationStrategy(HydrationStrategy):
    """Resolves nodes that declare an ``href`` attribute by merging external XML."""

    def __init__(self, fetcher: Optional[ResourceFetcher] = None) -> None:
        if fetcher is not None:
            self._fetcher = fetcher
        else:
            fetchers: List[ResourceFetcher] = [FileResourceFetcher()]
            try:  # pragma: no cover - optional dependency
                fetchers.append(S3ResourceFetcher())
            except ResourceFetchError:
                pass
            self._fetcher = CompositeResourceFetcher(fetchers)

        self._document_cache: Dict[str, etree._Element] = {}

    def apply(
        self,
        items: Sequence[HydrationItem],
        document_root: etree._Element,
        engine: HydrationEngine,
    ) -> List[HydrationItem]:
        for item in items:
            self._hydrate_href_nodes(item.element)
        return list(items)

    def _hydrate_href_nodes(self, element: etree._Element) -> None:
        # Continue resolving until no href attributes remain.
        while True:
            nodes_with_href = element.xpath(".//*[@href]")
            if not nodes_with_href:
                break
            for node in nodes_with_href:
                self._hydrate_single_node(node)

    def _hydrate_single_node(self, node: etree._Element) -> None:
        href_value = node.get("href")
        if not href_value:
            raise HydrationError(
                f"Element <{node.tag}> has an empty href attribute and cannot be hydrated."
            )

        xpath = node.getroottree().getpath(node)
        remote_root = self._get_remote_document(href_value)
        remote_node = self._locate_remote_node(node, remote_root, xpath, href_value)
        merged = self._merge_nodes(node, remote_node)
        merged.attrib.pop("href", None)

        parent = node.getparent()
        merged.tail = node.tail
        if parent is None:
            # Replace root node in place
            node.clear()
            node.tag = merged.tag
            node.attrib.update(merged.attrib)
            node.text = merged.text
            node[:] = merged[:]  # type: ignore[index]
        else:
            index = parent.index(node)
            parent.remove(node)
            parent.insert(index, merged)

    def _get_remote_document(self, uri: str) -> etree._Element:
        if uri not in self._document_cache:
            try:
                data = self._fetcher.fetch(uri)
            except ResourceFetchError as exc:
                raise HydrationError(str(exc)) from exc
            try:
                self._document_cache[uri] = etree.fromstring(data)
            except etree.XMLSyntaxError as exc:
                raise HydrationError(f"Unable to parse XML from '{uri}'.") from exc
        return self._document_cache[uri]

    def _locate_remote_node(
        self,
        local: etree._Element,
        remote_root: etree._Element,
        xpath: str,
        href_value: str,
    ) -> etree._Element:
        matches = remote_root.xpath(xpath)
        if len(matches) == 1:
            return matches[0]

        for attr in ("name", "id"):
            value = local.get(attr)
            if not value:
                continue
            attr_matches = [elem for elem in remote_root.iter(local.tag) if elem.get(attr) == value]
            if len(attr_matches) == 1:
                return attr_matches[0]

        tag_matches = list(remote_root.iter(local.tag))
        if len(tag_matches) == 1:
            return tag_matches[0]

        raise HydrationError(
            f"Remote document at '{href_value}' does not contain a single match for XPath '{xpath}'."
        )

    def _merge_nodes(self, local: etree._Element, remote: etree._Element) -> etree._Element:
        merged = etree.Element(remote.tag, nsmap=remote.nsmap)

        # Attributes: start with remote, overlay local (excluding href).
        for key, value in remote.attrib.items():
            merged.set(key, value)

        for key, value in local.attrib.items():
            if key == "href":
                continue
            merged.set(key, value)

        # Text precedence: use local text when non-empty.
        merged.text = local.text if local.text and local.text.strip() else remote.text
        merged.tail = local.tail

        # Merge children with precedence to local content.
        remote_children = list(remote)
        remote_lookup = {
            self._child_key(child, idx): child for idx, child in enumerate(remote_children)
        }
        consumed_keys = set()

        merged_children: List[etree._Element] = []
        for idx, local_child in enumerate(local):
            key = self._child_key(local_child, idx)
            if key in remote_lookup:
                merged_child = self._merge_nodes(local_child, remote_lookup[key])
                consumed_keys.add(key)
            else:
                merged_child = copy.deepcopy(local_child)
            merged_children.append(merged_child)

        local_signatures = {self._child_signature(child) for child in local}

        for idx, remote_child in enumerate(remote_children):
            key = self._child_key(remote_child, idx)
            if key in consumed_keys:
                continue
            if self._child_signature(remote_child) in local_signatures:
                continue
            merged_children.append(copy.deepcopy(remote_child))

        merged[:] = merged_children
        return merged

    def _child_key(self, element: etree._Element, position: int) -> Tuple[str, Optional[str], Optional[str], int]:
        for attr in ("name", "id"):
            if attr in element.attrib:
                return (element.tag, attr, element.get(attr), 0)
        return (element.tag, None, None, position)

    def _child_signature(self, element: etree._Element) -> Tuple[str, Optional[str], Optional[str]]:
        for attr in ("name", "id"):
            if attr in element.attrib:
                return (element.tag, attr, element.get(attr))
        return (element.tag, None, None)


def _strip_namespace(value: str) -> Tuple[str, str]:
    try:
        prefix, func = value.split(":", 1)
    except ValueError as exc:  # pragma: no cover - validated upstream
        raise HydrationError(f"Invalid use attribute '{value}'; expected prefix:function format.") from exc
    return prefix, func


class UseFunctionHydrationStrategy(HydrationStrategy):
    """Expands nodes that declare custom hydration functions via ``use`` attributes."""

    SUPPORTED_NAMESPACE = "vn"
    SUPPORTED_FUNCTIONS = {"link"}

    def apply(
        self,
        items: Sequence[HydrationItem],
        document_root: etree._Element,
        engine: HydrationEngine,
    ) -> List[HydrationItem]:
        output: List[HydrationItem] = []
        for item in items:
            queue: Deque[HydrationItem] = deque([item])
            while queue:
                current = queue.popleft()
                use_attr = current.element.get("use")
                if not use_attr:
                    output.append(current)
                    continue

                clones = self._expand_use(current, use_attr, document_root)
                if not clones:
                    raise HydrationError(
                        f"Custom function '{use_attr}' did not resolve to any target nodes."
                    )
                queue.extend(clones)
        return output

    def _expand_use(
        self,
        item: HydrationItem,
        use_attr: str,
        document_root: etree._Element,
    ) -> List[HydrationItem]:
        prefix, remainder = _strip_namespace(use_attr.split("(", 1)[0])
        if prefix != self.SUPPORTED_NAMESPACE:
            raise HydrationError(
                f"Unsupported custom hydration namespace '{prefix}' in '{use_attr}'."
            )

        function_name, args = self._parse_use_expression(use_attr)
        if function_name not in self.SUPPORTED_FUNCTIONS:
            raise HydrationError(
                f"Unsupported custom hydration function '{function_name}'."
            )

        if function_name == "link":
            return self._execute_link(item, args, document_root)

        raise HydrationError(f"Unhandled custom hydration function '{function_name}'.")

    def _parse_use_expression(self, expr: str) -> Tuple[str, Tuple[str, str]]:
        if not expr.endswith(")"):
            raise HydrationError(f"Invalid use attribute '{expr}'; expected parentheses.")
        prefix_and_func, args_str = expr[:-1].split("(", 1)
        _, func = _strip_namespace(prefix_and_func)
        parts = [part.strip() for part in args_str.split(",") if part.strip()]
        if len(parts) != 2:
            raise HydrationError(
                f"Custom function '{func}' expects exactly two arguments; received {len(parts)}."
            )
        return func, (parts[0], parts[1])

    def _execute_link(
        self,
        item: HydrationItem,
        args: Tuple[str, str],
        document_root: etree._Element,
    ) -> List[HydrationItem]:
        source_xpath, child_name = args
        matches = document_root.xpath(source_xpath)
        if not matches:
            raise HydrationError(
                f"vn:link source XPath '{source_xpath}' did not resolve to any elements."
            )

        produced: List[HydrationItem] = []
        for match in matches:
            if not isinstance(match, etree._Element):
                continue
            children = match.xpath(f"./{child_name}")
            if not children:
                continue
            for child in children:
                if not isinstance(child, etree._Element):
                    continue
                clone = copy.deepcopy(item.element)
                clone.attrib.pop("use", None)
                produced.append(HydrationItem(element=clone, context_node=child))

        return produced


class SelectHydrationStrategy(HydrationStrategy):
    """Resolves ``select`` attributes by cloning referenced nodes."""

    def __init__(self) -> None:
        self._reference_cache: dict[str, etree._Element] = {}

    def apply(
        self,
        items: Sequence[HydrationItem],
        document_root: etree._Element,
        engine: HydrationEngine,
    ) -> List[HydrationItem]:
        processed: List[HydrationItem] = []
        for item in items:
            element = item.element
            nodes_with_select = element.xpath(".//*[@select]")
            for node in nodes_with_select:
                if any(ancestor.get("use") for ancestor in node.iterancestors()):
                    continue
                select_expr = node.get("select")
                if not select_expr:
                    raise HydrationError("Encountered select attribute without a value during hydration.")
                replacement_source = self._resolve_reference(select_expr, document_root, item.context_node)
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

            processed.append(item)
        return processed

    def _resolve_reference(
        self,
        select_expr: str,
        document_root: etree._Element,
        context_node: Optional[etree._Element],
    ) -> etree._Element:
        if select_expr.startswith("/"):
            cache_key = select_expr
            if cache_key not in self._reference_cache:
                matches = document_root.xpath(select_expr)
                self._reference_cache[cache_key] = self._validate_match(select_expr, matches)
            return self._reference_cache[cache_key]

        if not select_expr.startswith("."):
            raise HydrationError(
                f"Select expression '{select_expr}' must be absolute or relative to the custom function context."
            )

        if context_node is None:
            raise HydrationError(
                f"Select expression '{select_expr}' requires a context node provided by a custom function."
            )

        if select_expr == ".":
            return context_node

        matches = context_node.xpath(select_expr)
        return self._validate_match(select_expr, matches)

    def _validate_match(self, select_expr: str, matches: Sequence[object]) -> etree._Element:
        elements = [match for match in matches if isinstance(match, etree._Element)]
        if len(elements) != 1:
            raise HydrationError(
                f"Select expression '{select_expr}' resolved to {len(elements)} elements; expected exactly one."
            )
        return elements[0]
