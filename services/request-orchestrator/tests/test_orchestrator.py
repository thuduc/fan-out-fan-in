import json
import os
import unittest

from redis import Redis

from app.orchestrator import RequestOrchestrator
from app.constants import (
    REQUEST_LIFECYCLE_STREAM,
    TASK_UPDATES_STREAM,
)
from app.processor import TaskProcessor

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/14")


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
            RequestOrchestrator(self.redis, task_invoker=_FailingInvoker()).run({
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
