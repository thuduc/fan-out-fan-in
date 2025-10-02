from __future__ import annotations

import pathlib
from urllib.parse import urlparse

from .base import ResourceFetcher, ResourceFetchError


class FileResourceFetcher(ResourceFetcher):
    """Fetches XML resources from local filesystem using file:// URIs or paths."""

    def supports(self, uri: str) -> bool:
        """Support file:// URIs and plain file paths.

        """
        parsed = urlparse(uri)
        return parsed.scheme in {"", "file"}

    def fetch(self, uri: str) -> bytes:
        """Read file contents from filesystem.

        """
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            path = pathlib.Path(parsed.path)
        else:
            path = pathlib.Path(uri)

        if not path.exists():
            raise ResourceFetchError(f"File not found for URI '{uri}'.")
        return path.read_bytes()

