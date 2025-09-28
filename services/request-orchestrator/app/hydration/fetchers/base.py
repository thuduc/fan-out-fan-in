from __future__ import annotations

from abc import ABC, abstractmethod


class ResourceFetchError(RuntimeError):
    """Raised when a resource cannot be retrieved."""


class ResourceFetcher(ABC):
    """Interface for retrieving external XML resources."""

    @abstractmethod
    def supports(self, uri: str) -> bool:
        """Return True if this fetcher can handle the given URI."""

    @abstractmethod
    def fetch(self, uri: str) -> bytes:
        """Retrieve the resource contents as raw bytes."""

