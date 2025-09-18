"""Local entry point to invoke RequestOrchestrator without AWS Lambda."""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sys

SERVICE_ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(SERVICE_ROOT)
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from redis import Redis

from app.orchestrator import RequestOrchestrator
from app.task_invoker import TaskInvoker

TASK_PROCESSOR_PACKAGE_ROOT = os.path.abspath(os.path.join(PACKAGE_ROOT, '..', 'task-processor'))


def _load_task_processor_cls():
    package_path = os.path.join(TASK_PROCESSOR_PACKAGE_ROOT, 'app')
    package_init = os.path.join(package_path, '__init__.py')
    package_name = 'task_processor_app'

    package_spec = importlib.util.spec_from_file_location(
        package_name,
        package_init,
        submodule_search_locations=[package_path],
    )
    if package_spec is None or package_spec.loader is None:
        raise ImportError('Unable to load task processor package')
    package_module = importlib.util.module_from_spec(package_spec)
    sys.modules[package_name] = package_module
    package_spec.loader.exec_module(package_module)  # type: ignore[attr-defined]

    processor_path = os.path.join(package_path, 'processor.py')
    processor_spec = importlib.util.spec_from_file_location(
        f'{package_name}.processor',
        processor_path,
    )
    if processor_spec is None or processor_spec.loader is None:
        raise ImportError('Unable to load task processor module')
    processor_module = importlib.util.module_from_spec(processor_spec)
    sys.modules[processor_spec.name] = processor_module
    processor_spec.loader.exec_module(processor_module)  # type: ignore[attr-defined]
    return processor_module.TaskProcessor


TaskProcessor = _load_task_processor_cls()


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
        task_invoker = _LocalTaskInvoker(redis_client, logger)
        orchestrator = RequestOrchestrator(redis_client, logger=logger, task_invoker=task_invoker)
        orchestrator.run(event)
    finally:
        try:
            redis_client.close()
        except AttributeError:
            # Older redis-py releases expose disconnect instead of close.
            redis_client.connection_pool.disconnect()

    return 0


class _LocalTaskInvoker(TaskInvoker):
    """Synchronous task invoker used by the integration harness."""

    def __init__(self, redis_client, logger):
        super().__init__(client=_StubLambdaClient(), function_name="local-task-processor", logger=logger)
        self._processor = TaskProcessor(redis_client, logger=logger)

    def invoke_async(self, payload: dict) -> None:  # type: ignore[override]
        self._processor.handle_dispatch({"values": payload})


class _StubLambdaClient:
    def invoke(self, **_kwargs):
        raise NotImplementedError("Local task invoker should not call invoke()")


if __name__ == "__main__":
    sys.exit(main())
