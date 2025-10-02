from __future__ import annotations

from typing import Iterable, List

from .base import ResourceFetcher, ResourceFetchError


class CompositeResourceFetcher(ResourceFetcher):
    """Composite fetcher that delegates to first supporting fetcher.

    Tries each registered fetcher in order until one supports the URI scheme.
    """

    def __init__(self, fetchers: Iterable[ResourceFetcher]) -> None:
        """Initialize with ordered list of fetchers.

        """
        self._fetchers: List[ResourceFetcher] = list(fetchers)

    def supports(self, uri: str) -> bool:
        """Check if any registered fetcher supports the URI.

        """
        return any(fetcher.supports(uri) for fetcher in self._fetchers)

    def fetch(self, uri: str) -> bytes:
        """Fetch using first supporting fetcher.

        """
        for fetcher in self._fetchers:
            if fetcher.supports(uri):
                return fetcher.fetch(uri)
        raise ResourceFetchError(f"No fetcher available to handle URI '{uri}'.")

