import importlib.util
import os
import sys
import unittest

from redis import Redis

os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6379/14')

# from app.hydrator import XmlHydrator
from app.orchestrator import RequestOrchestrator
from app.constants import (
    REQUEST_LIFECYCLE_STREAM,
    TASK_UPDATES_STREAM,
)

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/14")


def _load_task_processor_cls():
    """Dynamically load the task processor from the sibling service for tests."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'task-processor', 'app'))
    processor_path = os.path.join(base_dir, 'processor.py')
    spec = importlib.util.spec_from_file_location('task_processor.test_processor', processor_path)
    if spec is None or spec.loader is None:
        raise ImportError('Unable to load task processor module for tests')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module.TaskProcessor


TaskProcessor = _load_task_processor_cls()


def create_redis():
    return Redis.from_url(REDIS_URL, decode_responses=True)


class RequestOrchestratorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.redis = create_redis()
        self.redis.flushdb()
        processor = TaskProcessor(self.redis)
        self.invoker = _InlineTaskInvoker(processor)
        self.orchestrator = RequestOrchestrator(task_invoker=self.invoker)

    def tearDown(self):
        try:
            self.redis.flushdb()
        finally:
            self.redis.close()

    def test_full_happy_path(self):
        request_id = "req-integration-1"
        xml_key = f"cache:request:{request_id}:xml"
        response_key = f"cache:request:{request_id}:response"
        xml = (
            "<req><project><market name='m1'/><model name='mod1'/><group name='g1'>"
            "<valuation name='security'><instrument ref-name='i1'/></valuation></group>"
            "<group name='g2'><valuation name='schedule'><instrument ref-name='i2'/></valuation>"
            "<valuation name='analytics'><instrument ref-name='i2'/></valuation></group></project></req>"
        )
        self.redis.set(xml_key, xml)

        response = self.orchestrator.run({
            "requestId": request_id,
            "xmlKey": xml_key,
            "responseKey": response_key,
        })

        self.assertEqual(response["responseKey"], response_key)
        stored_response = self.redis.get(response_key)
        self.assertIn("<response", stored_response)

        lifecycle_entries = self.redis.xrange(REQUEST_LIFECYCLE_STREAM, '-', '+')
        statuses = []
        for _, fields in lifecycle_entries:
            status = fields.get('status') if isinstance(fields, dict) else None
            if status:
                statuses.append(status)
        self.assertIn('started', statuses)
        self.assertIn('completed', statuses)

    def test_group_failure_raises(self):
        request_id = "req-integration-2"
        xml_key = f"cache:request:{request_id}:xml"
        xml = (
            "<req><project><group name='g1'>"
            "<valuation name='security'><instrument ref-name='i1'/></valuation></group></project></req>"
        )
        self.redis.set(xml_key, xml)

        with self.assertRaises(RuntimeError):
            RequestOrchestrator(task_invoker=_FailingInvoker()).run({
                "requestId": request_id,
                "xmlKey": xml_key,
            })

        failure_payload = self.redis.get(f"cache:request:{request_id}:failure")
        self.assertIsNotNone(failure_payload)

    # ------------------------------------------------------------------


class _InlineTaskInvoker:
    def __init__(self, processor: TaskProcessor):
        self._processor = processor
        self.invocations = []

    def invoke_async(self, payload: dict) -> None:
        self.invocations.append(payload)
        self._processor.handle_dispatch({"values": payload})


class _FailingInvoker:
    def invoke_async(self, payload: dict) -> None:  # pylint: disable=unused-argument
        raise RuntimeError('forced-failure')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
