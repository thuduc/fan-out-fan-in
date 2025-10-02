from __future__ import annotations

from abc import ABC, abstractmethod


class ResourceFetchError(RuntimeError):
    """Raised when a resource cannot be retrieved or is inaccessible."""


class ResourceFetcher(ABC):
    """Abstract interface for retrieving external XML resources by URI.

    Implementations handle specific URI schemes (file://, s3://, etc.).
    """

    @abstractmethod
    def supports(self, uri: str) -> bool:
        """Check if this fetcher can handle the given URI scheme.

        """

    @abstractmethod
    def fetch(self, uri: str) -> bytes:
        """Fetch resource contents as raw bytes.

        """

