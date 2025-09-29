from __future__ import annotations

import pathlib
from urllib.parse import urlparse

from hydration.fetchers.base import ResourceFetcher, ResourceFetchError


class FileResourceFetcher(ResourceFetcher):
    """Fetches resources from the local filesystem."""

    def supports(self, uri: str) -> bool:
        parsed = urlparse(uri)
        return parsed.scheme in {"", "file"}

    def fetch(self, uri: str) -> bytes:
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            path = pathlib.Path(parsed.path)
        else:
            path = pathlib.Path(uri)

        if not path.exists():
            raise ResourceFetchError(f"File not found for URI '{uri}'.")
        return path.read_bytes()

