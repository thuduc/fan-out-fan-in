from __future__ import annotations

from typing import Iterable, List

from hydration.fetchers.base import ResourceFetcher, ResourceFetchError


class CompositeResourceFetcher(ResourceFetcher):
    """Delegates fetching to the first supporting fetcher."""

    def __init__(self, fetchers: Iterable[ResourceFetcher]) -> None:
        self._fetchers: List[ResourceFetcher] = list(fetchers)

    def supports(self, uri: str) -> bool:
        return any(fetcher.supports(uri) for fetcher in self._fetchers)

    def fetch(self, uri: str) -> bytes:
        for fetcher in self._fetchers:
            if fetcher.supports(uri):
                return fetcher.fetch(uri)
        raise ResourceFetchError(f"No fetcher available to handle URI '{uri}'.")

