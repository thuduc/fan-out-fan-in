"""Local entry point to invoke RequestOrchestrator without AWS Lambda."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

SERVICE_ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(SERVICE_ROOT)
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from redis import Redis

from app.orchestrator import RequestOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RequestOrchestrator locally")
    parser.add_argument("payload", help="JSON payload passed to the orchestrator")
    parser.add_argument("redis_url", help="Redis connection URL")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    event = json.loads(args.payload)

    logger = logging.getLogger("request-orchestrator.local-runner")
    logger.setLevel(logging.INFO)

    redis_client = Redis.from_url(args.redis_url, decode_responses=True)
    try:
        xml_key = event.get("xmlKey")
        if xml_key and not redis_client.exists(xml_key):
            raise ValueError(f"XML payload {xml_key} is missing before invocation")
        orchestrator = RequestOrchestrator(redis_client, logger=logger)
        orchestrator.run(event)
    finally:
        try:
            redis_client.close()
        except AttributeError:
            # Older redis-py releases expose disconnect instead of close.
            redis_client.connection_pool.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
