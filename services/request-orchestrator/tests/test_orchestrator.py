import json
import os
import threading
import unittest

from redis import Redis

from app.orchestrator import RequestOrchestrator
from app.constants import (
    REQUEST_LIFECYCLE_STREAM,
    TASK_DISPATCH_STREAM,
    TASK_UPDATES_STREAM,
)

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/14")


def create_redis():
    return Redis.from_url(REDIS_URL, decode_responses=True)


class RequestOrchestratorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.redis = create_redis()
        self.redis.flushdb()
        self.orchestrator = RequestOrchestrator(self.redis)

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

        stop_worker, worker_thread = self._start_worker(task_count=3)

        response = self.orchestrator.run({
            "requestId": request_id,
            "xmlKey": xml_key,
            "responseKey": response_key,
        })

        stop_worker.set()
        worker_thread.join(timeout=1)

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

        stop_worker, worker_thread = self._start_failure_worker()

        with self.assertRaises(RuntimeError):
            self.orchestrator.run({
                "requestId": request_id,
                "xmlKey": xml_key,
            })

        stop_worker.set()
        worker_thread.join(timeout=1)

    # ------------------------------------------------------------------

    def _start_worker(self, task_count):
        stop_event = threading.Event()

        def _worker():
            stream_state = {TASK_DISPATCH_STREAM: '0-0'}
            processed = 0
            while not stop_event.is_set() and processed < task_count:
                entries = self.redis.xread(stream_state, count=task_count, block=200)
                if not entries:
                    continue
                for stream_name, messages in entries:
                    if stream_name != TASK_DISPATCH_STREAM:
                        continue
                    for message_id, values in messages:
                        stream_state[TASK_DISPATCH_STREAM] = message_id
                        processed += 1
                        result_key = values['resultKey']
                        self.redis.set(result_key, json.dumps({'processed': processed}))
                        update = {
                            'requestId': values['requestId'],
                            'groupIdx': values['groupIdx'],
                            'taskId': values['taskId'],
                            'resultKey': result_key,
                            'status': 'completed',
                            'attempt': values.get('attempt', '1'),
                            'result': json.dumps({'processed': processed}),
                        }
                        self.redis.xadd(TASK_UPDATES_STREAM, update)
            stop_event.set()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return stop_event, thread

    def _start_failure_worker(self):
        stop_event = threading.Event()

        def _worker():
            stream_state = {TASK_DISPATCH_STREAM: '0-0'}
            sent_failure = False
            while not stop_event.is_set() and not sent_failure:
                entries = self.redis.xread(stream_state, count=1, block=200)
                if not entries:
                    continue
                for _, messages in entries:
                    for message_id, values in messages:
                        stream_state[TASK_DISPATCH_STREAM] = message_id
                        update = {
                            'requestId': values['requestId'],
                            'groupIdx': values['groupIdx'],
                            'taskId': values['taskId'],
                            'resultKey': values['resultKey'],
                            'status': 'failed',
                            'attempt': '3',
                            'result': json.dumps({'error': 'forced'}),
                        }
                        self.redis.xadd(TASK_UPDATES_STREAM, update)
                        sent_failure = True
                        break
            stop_event.set()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return stop_event, thread


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
