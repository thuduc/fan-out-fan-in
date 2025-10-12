from __future__ import annotations

import copy
from collections import deque
from typing import Deque, Dict, List, Optional, Sequence, Tuple, Set

from lxml import etree

try:
    from ..exceptions import HydrationError
except ImportError:
    from exceptions import HydrationError

try:
    from .engine import HydrationItem, HydrationStrategy, HydrationEngine
    from .fetchers.base import ResourceFetcher, ResourceFetchError
    from .fetchers.composite import CompositeResourceFetcher
    from .fetchers.file import FileResourceFetcher
    from .fetchers.s3 import S3ResourceFetcher
except ImportError:
    from hydration.engine import HydrationItem, HydrationStrategy, HydrationEngine
    from hydration.fetchers.base import ResourceFetcher, ResourceFetchError
    from hydration.fetchers.composite import CompositeResourceFetcher
    from hydration.fetchers.file import FileResourceFetcher
    from hydration.fetchers.s3 import S3ResourceFetcher


def _child_key(element: etree._Element, position: int) -> Tuple[str, Optional[str], Optional[str], int]:
    """Generate unique key for child element matching based on tag and identity attributes.

    """
    for attr in ("name", "id"):
        if attr in element.attrib:
            return (element.tag, attr, element.get(attr), 0)
    return (element.tag, None, None, position)


def _child_signature(element: etree._Element) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate signature for child element based on tag and identity attributes.

    """
    for attr in ("name", "id"):
        if attr in element.attrib:
            return (element.tag, attr, element.get(attr))
    return (element.tag, None, None)


def _merge_elements(
    local: etree._Element,
    remote: etree._Element,
    *,
    ignore_local_attrs: Iterable[str] = (),
    ignore_remote_attrs: Iterable[str] = (),
) -> etree._Element:
    """Merge two XML elements with local taking precedence over remote.

    Combines attributes, text, and children from both elements. Local attributes
    override remote attributes. Children are matched by identity (name/id) and
    recursively merged.

    """

    local_ignore = set(ignore_local_attrs)
    remote_ignore = set(ignore_remote_attrs)

    merged = etree.Element(remote.tag, nsmap=remote.nsmap)

    for key, value in remote.attrib.items():
        if key in remote_ignore:
            continue
        merged.set(key, value)

    for key, value in local.attrib.items():
        if key in local_ignore:
            continue
        merged.set(key, value)

    if "select" in local.attrib and remote.text is not None:
        merged.text = remote.text
    else:
        merged.text = local.text if local.text and local.text.strip() else remote.text
    merged.tail = local.tail

    remote_children = list(remote)
    remote_lookup = {_child_key(child, idx): child for idx, child in enumerate(remote_children)}
    consumed_keys: set[Tuple[str, Optional[str], Optional[str], int]] = set()
    consumed_signatures: set[Tuple[str, Optional[str], Optional[str]]] = set()

    merged_children: List[etree._Element] = []
    for idx, local_child in enumerate(local):
        key = _child_key(local_child, idx)
        remote_child = remote_lookup.get(key)

        if remote_child is None:
            signature = _child_signature(local_child)
            for r_idx, candidate in enumerate(remote_children):
                candidate_key = _child_key(candidate, r_idx)
                if candidate_key in consumed_keys:
                    continue
                if _child_signature(candidate) == signature:
                    remote_child = candidate
                    key = candidate_key
                    break

        if remote_child is not None:
            merged_child = _merge_elements(
                local_child,
                remote_child,
                ignore_local_attrs=local_ignore,
                ignore_remote_attrs=remote_ignore,
            )
            consumed_keys.add(key)
            consumed_signatures.add(_child_signature(remote_child))
        else:
            merged_child = copy.deepcopy(local_child)

        merged_children.append(merged_child)

    local_signatures = {_child_signature(child) for child in local}

    for idx, remote_child in enumerate(remote_children):
        key = _child_key(remote_child, idx)
        if key in consumed_keys:
            continue
        signature = _child_signature(remote_child)
        if signature in local_signatures and signature in consumed_signatures:
            continue
        merged_children.append(copy.deepcopy(remote_child))

    merged[:] = merged_children
    return merged


_SELECT_PLACEHOLDER_PREFIX = "${select("
_SELECT_PLACEHOLDER_SUFFIX = ")}"


def _evaluate_single_xpath(
    xpath_expr: str,
    document_root: etree._Element,
    context_node: Optional[etree._Element],
    *,
    context_label: str,
) -> str:
    expression = xpath_expr.strip()
    if not expression:
        raise HydrationError(f"{context_label} must not be empty.")

    if expression.startswith("/"):
        results = document_root.xpath(expression)
    else:
        if context_node is None:
            raise HydrationError(
                f"{context_label} '{expression}' requires a context node provided by a custom function."
            )
        results = context_node.xpath(expression)

    if not results:
        raise HydrationError(
            f"{context_label} '{expression}' did not resolve to any values."
        )
    if len(results) != 1:
        raise HydrationError(
            f"{context_label} '{expression}' resolved to {len(results)} values; expected exactly one."
        )

    value = results[0]
    if isinstance(value, etree._Element):
        return etree.tostring(value, encoding="unicode")
    if isinstance(value, bytes):
        return value.decode("UTF-8")
    return str(value)


def _to_xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'

    parts = value.split("'")
    pieces: List[str] = []
    for idx, part in enumerate(parts):
        if part:
            pieces.append(f"'{part}'")
        else:
            pieces.append("''")
        if idx != len(parts) - 1:
            pieces.append('"\'"')
    return "concat(" + ", ".join(pieces) + ")"


def _extract_select_placeholder(expression: str, start: int) -> Tuple[str, int]:
    cursor = start + len(_SELECT_PLACEHOLDER_PREFIX)
    depth = 1
    while cursor < len(expression):
        char = expression[cursor]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                break
        cursor += 1

    if depth != 0 or cursor >= len(expression):
        raise HydrationError("Malformed ${select(...)} placeholder: missing closing parenthesis.")
    if cursor + 1 >= len(expression) or expression[cursor + 1] != "}":
        raise HydrationError("Malformed ${select(...)} placeholder: expected closing '}'.")

    inner = expression[start + len(_SELECT_PLACEHOLDER_PREFIX):cursor].strip()
    return inner, cursor + 2


def _substitute_select_placeholders(
    expression: str,
    document_root: etree._Element,
    context_node: Optional[etree._Element],
) -> str:
    if _SELECT_PLACEHOLDER_PREFIX not in expression:
        return expression

    parts: List[str] = []
    index = 0
    length = len(expression)

    while index < length:
        start = expression.find(_SELECT_PLACEHOLDER_PREFIX, index)
        if start == -1:
            parts.append(expression[index:])
            break

        placeholder_expression, end = _extract_select_placeholder(expression, start)

        prefix_end = start
        suffix_start = end
        if start > index and end < length:
            prev_char = expression[start - 1]
            next_char = expression[end]
            if prev_char == next_char and prev_char in ("'", '"'):
                prefix_end = start - 1
                suffix_start = end + 1

        parts.append(expression[index:prefix_end])

        resolved_value = _evaluate_single_xpath(
            placeholder_expression,
            document_root,
            context_node,
            context_label="Select expression",
        )
        parts.append(_to_xpath_literal(resolved_value))

        index = suffix_start

    return "".join(parts)


def _split_xpath_alternatives(expression: str) -> List[str]:
    alternatives: List[str] = []
    current: List[str] = []
    depth = 0
    in_single_quote = False
    in_double_quote = False

    for char in expression:
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif char in ("[", "(") and not in_single_quote and not in_double_quote:
            depth += 1
        elif char in ("]", ")") and not in_single_quote and not in_double_quote:
            depth = max(0, depth - 1)

        if char == "|" and depth == 0 and not in_single_quote and not in_double_quote:
            alternative = "".join(current).strip()
            if alternative:
                alternatives.append(alternative)
            current = []
        else:
            current.append(char)

    tail = "".join(current).strip()
    if tail:
        alternatives.append(tail)

    return alternatives or [expression.strip()]


class HrefHydrationStrategy(HydrationStrategy):
    """Resolves nodes with href attributes by fetching and merging external XML.

    Fetches XML documents from file:// or s3:// URIs and merges them with local
    elements. The local element's attributes and children take precedence over
    the remote content. Caches fetched documents for reuse.
    """

    def __init__(self, fetcher: Optional[ResourceFetcher] = None) -> None:
        """Initialize href hydration strategy.

        """
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
        """Apply href hydration to all items.

        """
        for item in items:
            self._hydrate_href_nodes(item.element)
        return list(items)

    def _hydrate_href_nodes(self, element: etree._Element) -> None:
        """Recursively hydrate all descendant nodes with href attributes.

        """
        # Continue resolving until no href attributes remain.
        while True:
            nodes_with_href = element.xpath(".//*[@href]")
            if not nodes_with_href:
                break
            for node in nodes_with_href:
                self._hydrate_single_node(node)

    def _hydrate_single_node(self, node: etree._Element) -> None:
        """Fetch and merge remote XML for a single node with href attribute.

        """
        href_value = node.get("href")
        if not href_value:
            raise HydrationError(
                f"Element <{node.tag}> has an empty href attribute and cannot be hydrated."
            )

        xpath = node.getroottree().getpath(node)
        remote_root = self._get_remote_document(href_value)
        remote_node = self._locate_remote_node(node, remote_root, xpath, href_value)
        merged = _merge_elements(
            node,
            remote_node,
            ignore_local_attrs={"href"},
            ignore_remote_attrs={"href"},
        )

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
        """Fetch and cache remote XML document by URI.

        """
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
        """Locate corresponding node in remote document using multiple strategies.

        Tries XPath first, then name/id attribute matching, then tag matching.

        """
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


def _strip_namespace(value: str) -> Tuple[str, str]:
    """Parse namespace prefix and function name from qualified name.

    """
    try:
        prefix, func = value.split(":", 1)
    except ValueError as exc:  # pragma: no cover - validated upstream
        raise HydrationError(f"Invalid use attribute '{value}'; expected prefix:function format.") from exc
    return prefix, func


class UseHydrationStrategy(HydrationStrategy):
    """Expands elements with vn:use function calls (e.g., vn:link).

    Supports vn:link(xpath, childName) which clones the element for each child
    node matched by the XPath, binding each clone to a specific context node.
    """

    SUPPORTED_NAMESPACE = "vn"
    SUPPORTED_FUNCTIONS = {"link"}

    def apply(
        self,
        items: Sequence[HydrationItem],
        document_root: etree._Element,
        engine: HydrationEngine,
    ) -> List[HydrationItem]:
        """Apply use function expansion, potentially multiplying items.

        """
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
        """Parse and execute use directive, returning cloned items."""
        stripped = use_attr.strip()
        if stripped.startswith(f"{self.SUPPORTED_NAMESPACE}:"):
            return self._expand_custom_use(item, stripped, document_root)
        return self._expand_plain_xpath(item, stripped, document_root)

    def _expand_plain_xpath(
        self,
        item: HydrationItem,
        xpath_expr: str,
        document_root: etree._Element,
    ) -> List[HydrationItem]:
        if not xpath_expr:
            raise HydrationError("use attribute must not be empty.")

        if xpath_expr.startswith("/"):
            matches = document_root.xpath(xpath_expr)
        else:
            context = item.context_node or item.element
            matches = context.xpath(xpath_expr)

        elements = [match for match in matches if isinstance(match, etree._Element)]
        if not elements:
            raise HydrationError(f"use XPath '{xpath_expr}' did not resolve to any elements.")

        clones: List[HydrationItem] = []
        for target in elements:
            clone = copy.deepcopy(item.element)
            clone.attrib.pop("use", None)
            clones.append(HydrationItem(element=clone, context_node=target))
        return clones

    def _expand_custom_use(
        self,
        item: HydrationItem,
        use_attr: str,
        document_root: etree._Element,
    ) -> List[HydrationItem]:
        prefix, _ = _strip_namespace(use_attr.split("(", 1)[0])
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
        """Parse use function expression into name and arguments."""
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
        """Execute vn:link function, cloning element for each matched child."""
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


class AttributeSelectHydrationStrategy(HydrationStrategy):
    """Replaces attribute placeholders of form ${select(xpath)} with XPath results.

    Evaluates XPath expressions embedded in attribute values and replaces
    placeholders with the text content of matched elements.
    """

    _PREFIX = "${select("
    _SUFFIX = ")}"

    def apply(
        self,
        items: Sequence[HydrationItem],
        document_root: etree._Element,
        engine: HydrationEngine,
    ) -> List[HydrationItem]:
        """Apply attribute select hydration to all items.

        """
        for item in items:
            self._hydrate_attributes(item, document_root)
        return list(items)

    def _hydrate_attributes(self, item: HydrationItem, document_root: etree._Element) -> None:
        """Hydrate all ${select(...)} placeholders in element attributes."""
        for element in item.element.iter():
            for attr_name, attr_value in list(element.attrib.items()):
                xpath_expr = self._extract_xpath(attr_value)
                if not xpath_expr:
                    continue
                resolved_value = self._resolve_xpath(
                    xpath_expr,
                    document_root,
                    context_node=item.context_node,
                )
                element.set(attr_name, resolved_value)

    def _extract_xpath(self, value: str) -> Optional[str]:
        if not value.startswith(self._PREFIX) or not value.endswith(self._SUFFIX):
            return None
        inner = value[len(self._PREFIX) : -len(self._SUFFIX)].strip()
        if not inner:
            raise HydrationError(
                "Attribute select placeholder must include a non-empty XPath expression."
            )
        return inner

    def _resolve_xpath(
        self,
        xpath_expr: str,
        document_root: etree._Element,
        *,
        context_node: Optional[etree._Element],
    ) -> str:
        return _evaluate_single_xpath(
            xpath_expr,
            document_root,
            context_node,
            context_label="Attribute select XPath",
        )


class SelectHydrationStrategy(HydrationStrategy):
    """Resolves ``select`` attributes by cloning referenced nodes."""

    def __init__(self) -> None:
        self._reference_cache: dict[str, etree._Element] = {}
        self._link_cache: dict[Tuple[str, str], Optional[etree._Element]] = {}

    def apply(
        self,
        items: Sequence[HydrationItem],
        document_root: etree._Element,
        engine: HydrationEngine,
    ) -> List[HydrationItem]:
        processed: List[HydrationItem] = []
        for item in items:
            element = item.element

            while True:
                nodes_with_select = [
                    node
                    for node in element.xpath(".//*[@select]")
                    if not any(ancestor.get("use") for ancestor in node.iterancestors())
                ]
                if not nodes_with_select:
                    break

                for node in nodes_with_select:
                    select_expr = node.get("select")
                    if not select_expr:
                        raise HydrationError(
                            "Encountered select attribute without a value during hydration."
                        )
                    replacement_source = self._resolve_reference(
                        select_expr,
                        document_root,
                        item.context_node,
                    )

                    parent = node.getparent()
                    if parent is None:
                        raise HydrationError(
                            f"Cannot hydrate element <{node.tag}> without a parent; select expression '{select_expr}' is invalid."
                        )

                    if replacement_source is None:
                        continue
                    if select_expr.strip().startswith("vn:link("):
                        merged = copy.deepcopy(replacement_source)
                    else:
                        merged = _merge_elements(node, replacement_source, ignore_local_attrs={"select"})

                    insertion_index = parent.index(node)
                    tail_text = node.tail
                    parent.remove(node)

                    hydrated_replacements = engine.hydrate_element(
                        merged,
                        document_root,
                        context_node=item.context_node,
                    )
                    if not hydrated_replacements:
                        raise HydrationError(
                            f"Hydration produced no nodes for select expression '{select_expr}'."
                        )

                    for offset, replacement_item in enumerate(hydrated_replacements):
                        replacement = replacement_item.element
                        replacement.attrib.pop("select", None)
                        if offset == len(hydrated_replacements) - 1:
                            replacement.tail = tail_text
                        else:
                            replacement.tail = None
                        parent.insert(insertion_index + offset, replacement)

            processed.append(item)
        return processed

    def _resolve_reference(
        self,
        select_expr: str,
        document_root: etree._Element,
        context_node: Optional[etree._Element],
    ) -> etree._Element:
        expanded_expr = _substitute_select_placeholders(select_expr, document_root, context_node)
        alternatives = _split_xpath_alternatives(expanded_expr)

        last_error: Optional[str] = None

        for candidate in alternatives:
            if not candidate:
                continue
            try:
                result = self._evaluate_candidate(candidate, document_root, context_node)
                if result is not None:
                    return result
            except HydrationError as exc:
                last_error = str(exc)
                continue

        message = last_error or f"Select expression '{expanded_expr}' did not resolve to any elements."
        raise HydrationError(message)

    def _evaluate_candidate(
        self,
        candidate: str,
        document_root: etree._Element,
        context_node: Optional[etree._Element],
    ) -> Optional[etree._Element]:
        stripped = candidate.strip()
        if stripped.startswith("vn:link(") and stripped.endswith(")"):
            return self._evaluate_link_expression(stripped, document_root, context_node)

        if candidate.startswith("/"):
            cache_key = candidate
            if cache_key not in self._reference_cache:
                matches = document_root.xpath(candidate)
                element = self._validate_optional_match(candidate, matches)
                self._reference_cache[cache_key] = element
            return self._reference_cache[cache_key]

        if context_node is None:
            raise HydrationError(
                f"Select expression '{candidate}' requires a context node provided by a custom function."
            )

        if candidate == ".":
            return context_node

        matches = context_node.xpath(candidate)
        return self._validate_optional_match(candidate, matches)

    def _evaluate_link_expression(
        self,
        expression: str,
        document_root: etree._Element,
        context_node: Optional[etree._Element],
    ) -> Optional[etree._Element]:
        inner = expression[len("vn:link("):-1]
        source_expr, child_expr = self._split_link_arguments(inner)
        cache_key = (source_expr, child_expr)
        if cache_key in self._link_cache:
            return self._link_cache[cache_key]

        sources = self._evaluate_link_source(source_expr, document_root, context_node)
        for source in sources:
            result = self._evaluate_link_child(child_expr, source)
            if result is not None:
                self._link_cache[cache_key] = result
                return result

        self._link_cache[cache_key] = None
        return None

    def _split_link_arguments(self, inner: str) -> Tuple[str, str]:
        parts: List[str] = []
        current: List[str] = []
        depth = 0
        in_single_quote = False
        in_double_quote = False

        for char in inner:
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
            elif char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
            elif char in ("(", "[") and not in_single_quote and not in_double_quote:
                depth += 1
            elif char in (")", "]") and not in_single_quote and not in_double_quote:
                depth = max(0, depth - 1)

            if char == "," and depth == 0 and not in_single_quote and not in_double_quote:
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
            else:
                current.append(char)

        tail = "".join(current).strip()
        if tail:
            parts.append(tail)

        if len(parts) != 2:
            raise HydrationError("vn:link requires exactly two arguments (sourceXPath, childXPath).")

        return parts[0], parts[1]

    def _evaluate_link_source(
        self,
        source_expr: str,
        document_root: etree._Element,
        context_node: Optional[etree._Element],
    ) -> List[etree._Element]:
        if source_expr.startswith("/"):
            matches = document_root.xpath(source_expr)
        else:
            if context_node is None:
                raise HydrationError(
                    f"vn:link source expression '{source_expr}' requires a context node provided by a custom function."
                )
            matches = context_node.xpath(source_expr)

        elements = [match for match in matches if isinstance(match, etree._Element)]
        return elements

    def _evaluate_link_child(self, child_expr: str, source: etree._Element) -> Optional[etree._Element]:
        if child_expr in (".", "self::node()", "self::*"):
            return source

        matches = source.xpath(child_expr)
        elements = [match for match in matches if isinstance(match, etree._Element)]
        if not elements:
            return None
        if len(elements) != 1:
            raise HydrationError(
                f"vn:link child expression '{child_expr}' resolved to {len(elements)} elements; expected exactly one."
            )
        return elements[0]

    def _validate_optional_match(self, select_expr: str, matches: Sequence[object]) -> Optional[etree._Element]:
        elements = [match for match in matches if isinstance(match, etree._Element)]
        if not elements:
            return None
        if len(elements) != 1:
            raise HydrationError(
                f"Select expression '{select_expr}' resolved to {len(elements)} elements; expected exactly one."
            )
        return elements[0]

    def _validate_match(self, select_expr: str, matches: Sequence[object]) -> etree._Element:
        elements = [match for match in matches if isinstance(match, etree._Element)]
        if len(elements) != 1:
            raise HydrationError(
                f"Select expression '{select_expr}' resolved to {len(elements)} elements; expected exactly one."
            )
        return elements[0]
