"""AWS Lambda entry point for vnas task processor.

Lambda is invoked by vnvs orchestrator for each individual task.
Executes valuation computation and stores result in Redis.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from redis import Redis

from processor import TaskProcessor

LOGGER = logging.getLogger(__name__)
_CLIENT = None
_PROCESSOR = None


def _get_redis_client() -> Redis:
    """Get or create singleton Redis client from REDIS_URL environment variable.

    """
    global _CLIENT  # noqa: PLW0603
    if _CLIENT is None:
        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            raise ValueError("REDIS_URL environment variable must be set")
        _CLIENT = Redis.from_url(redis_url, decode_responses=True)
    return _CLIENT


def _get_processor() -> TaskProcessor:
    """Get or create singleton TaskProcessor.

    """
    global _PROCESSOR  # noqa: PLW0603
    if _PROCESSOR is None:
        _PROCESSOR = TaskProcessor(_get_redis_client(), logger=LOGGER)
    return _PROCESSOR


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Lambda entry point invoked by vnvs orchestrator.

    """
    processor = _get_processor()
    result = processor.handle_dispatch({"values": event})
    return {
        "statusCode": 200,
        "body": json.dumps(result),
    }
