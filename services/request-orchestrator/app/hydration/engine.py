from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from lxml import etree

from ..exceptions import HydrationError


@dataclass
class HydrationItem:
    """Represents a node undergoing hydration along with its context."""

    element: etree._Element
    context_node: Optional[etree._Element] = None


class HydrationStrategy:
    """Protocol for hydration strategies."""

    def apply(
        self,
        items: Sequence[HydrationItem],
        document_root: etree._Element,
        engine: HydrationEngine,
    ) -> List[HydrationItem]:  # pragma: no cover - interface definition
        raise NotImplementedError


class HydrationEngine:
    """Coordinates registered hydration strategies to expand and hydrate XML nodes."""

    def __init__(self, strategies: Optional[Iterable[HydrationStrategy]] = None) -> None:
        if strategies is None:
            from .strategies import (
                HrefHydrationStrategy,
                SelectHydrationStrategy,
                UseFunctionHydrationStrategy,
            )

            href_strategy = HrefHydrationStrategy()
            strategies = [
                href_strategy,
                UseFunctionHydrationStrategy(),
                SelectHydrationStrategy(),
                href_strategy,
            ]

        self._strategies: List[HydrationStrategy] = list(strategies)

    def hydrate_element(
        self,
        element: etree._Element,
        document_root: etree._Element,
        *,
        context_node: Optional[etree._Element] = None,
    ) -> List[HydrationItem]:
        """Return fully hydrated copies of ``element``.

        The returned items contain deep-copied elements. Strategies may return
        multiple items when duplication is required (e.g., vn:link).
        """

        if element is None:
            raise HydrationError("Cannot hydrate a null element.")

        initial_item = HydrationItem(element=copy.deepcopy(element), context_node=context_node)
        return self._apply_strategies([initial_item], document_root)

    def _apply_strategies(
        self,
        items: Sequence[HydrationItem],
        document_root: etree._Element,
    ) -> List[HydrationItem]:
        hydrated_items: List[HydrationItem] = list(items)
        for strategy in self._strategies:
            hydrated_items = strategy.apply(hydrated_items, document_root, self)
        return hydrated_items

    def replace_strategies(self, strategies: Iterable[HydrationStrategy]) -> None:
        self._strategies = list(strategies)

    def append_strategy(self, strategy: HydrationStrategy) -> None:
        self._strategies.append(strategy)
