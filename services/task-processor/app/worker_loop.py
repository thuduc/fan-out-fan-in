"""Simple polling worker that executes TaskProcessor locally."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
import uuid
from typing import List, Tuple

SERVICE_ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(SERVICE_ROOT)
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from redis import Redis
from redis.exceptions import ResponseError

from app.constants import TASK_DISPATCH_STREAM
from app.processor import TaskProcessor


LOGGER = logging.getLogger("task-processor.worker")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run task processor worker loop locally")
    parser.add_argument("redis_url", help="Redis connection URL")
    parser.add_argument(
        "--group",
        default="task-workers",
        help="Consumer group name for the dispatch stream",
    )
    parser.add_argument(
        "--block-ms",
        type=int,
        default=1000,
        help="Block duration in milliseconds for XREADGROUP",
    )
    return parser.parse_args()


def ensure_consumer_group(redis_client: Redis, stream: str, group: str) -> None:
    try:
        redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:  # noqa: PERF203
        if "BUSYGROUP" not in str(exc):
            raise


def main() -> int:
    args = parse_args()
    LOGGER.setLevel(logging.INFO)

    redis_client = Redis.from_url(args.redis_url, decode_responses=True)
    processor = TaskProcessor(redis_client, logger=LOGGER)

    ensure_consumer_group(redis_client, TASK_DISPATCH_STREAM, args.group)
    consumer = f"local-{uuid.uuid4().hex}"

    stop_flag = {"value": False}

    def _signal_handler(signum, frame):  # noqa: ARG001
        stop_flag["value"] = True
        try:
            redis_client.connection_pool.disconnect()
        except AttributeError:
            redis_client.close()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        while not stop_flag["value"]:
            try:
                entries: List[Tuple[str, List[Tuple[str, dict]]]] = redis_client.xreadgroup(
                    groupname=args.group,
                    consumername=consumer,
                    streams={TASK_DISPATCH_STREAM: ">"},
                    count=5,
                    block=args.block_ms,
                )
            except ResponseError as exc:
                if "NOGROUP" in str(exc):
                    ensure_consumer_group(redis_client, TASK_DISPATCH_STREAM, args.group)
                    continue
                raise
            except (OSError, ValueError):
                if stop_flag["value"]:
                    break
                raise

            if not entries:
                continue

            for stream_name, messages in entries:
                if stream_name != TASK_DISPATCH_STREAM:
                    continue
                for entry_id, values in messages:
                    try:
                        processor.handle_dispatch({"id": entry_id, "values": values})
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("Task processing raised an exception", extra={"entry": entry_id})
                    finally:
                        redis_client.xack(TASK_DISPATCH_STREAM, args.group, entry_id)
    finally:
        try:
            redis_client.close()
        except AttributeError:
            redis_client.connection_pool.disconnect()
        time.sleep(0.05)

    return 0


if __name__ == "__main__":
    sys.exit(main())
