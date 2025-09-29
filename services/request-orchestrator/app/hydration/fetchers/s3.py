from __future__ import annotations

from urllib.parse import urlparse

from hydration.fetchers.base import ResourceFetcher, ResourceFetchError

try:  # pragma: no cover - optional dependency
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except Exception:  # pragma: no cover - boto3 not installed
    boto3 = None
    BotoCoreError = ClientError = Exception


class S3ResourceFetcher(ResourceFetcher):
    """Fetches resources stored in S3 buckets."""

    def __init__(self, client=None) -> None:
        if boto3 is None and client is None:
            raise ResourceFetchError(
                "boto3 is required for S3ResourceFetcher but is not installed."
            )
        self._client = client or boto3.client("s3")

    def supports(self, uri: str) -> bool:
        return urlparse(uri).scheme == "s3"

    def fetch(self, uri: str) -> bytes:
        parsed = urlparse(uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        if not bucket or not key:
            raise ResourceFetchError(f"Invalid S3 URI '{uri}'.")

        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        except (ClientError, BotoCoreError) as exc:  # pragma: no cover - network error cases
            raise ResourceFetchError(f"Failed to fetch '{uri}' from S3.") from exc
