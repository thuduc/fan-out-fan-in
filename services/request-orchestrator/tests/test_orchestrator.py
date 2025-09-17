import json
import unittest

from app.orchestrator import RequestOrchestrator
from app.constants import (
    REQUEST_LIFECYCLE_STREAM,
    TASK_DISPATCH_STREAM,
    TASK_UPDATES_STREAM,
)


class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.hashes = {}
        self.streams = {}
        self.groups = set()
        self.acks = []
        self.pending_updates = []

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value):
        self.kv[key] = value

    def hset(self, key, mapping):
        current = self.hashes.setdefault(key, {})
        current.update(mapping)

    def xadd(self, stream, _id, values):
        entries = self.streams.setdefault(stream, [])
        entry_id = f"{len(entries) + 1}-0"
        entries.append({"id": entry_id, "values": values})
        return entry_id

    def xgroupCreate(self, stream, group, _id, options=None):  # noqa: N802
        key = (stream, group)
        if key in self.groups:
            raise Exception("BUSYGROUP")
        self.groups.add(key)
        self.streams.setdefault(stream, [])

    def xreadgroup(self, params):
        if not self.pending_updates:
            return []
        entry = self.pending_updates.pop(0)
        # ensure result data is available
        result_key = entry["values"].get("resultKey")
        if result_key and result_key not in self.kv:
            self.kv[result_key] = json.dumps({"origin": "test"})
        return [entry]

    def xack(self, stream, group, entry_id):
        self.acks.append((stream, group, entry_id))


class RequestOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.orchestrator = RequestOrchestrator(self.redis)

    def test_full_happy_path(self):
        xml = """<req><project><market name='m1'/><model name='mod1'/><group name='g1'>\n        <valuation name='security'><instrument ref-name='i1'/></valuation></group>\n        <group name='g2'><valuation name='schedule'><instrument ref-name='i2'/></valuation>\n        <valuation name='analytics'><instrument ref-name='i2'/></valuation></group></project></req>"""
        self.redis.set("cache:request:req-1:xml", xml)

        # enqueue updates for three tasks
        self.redis.pending_updates.extend([
            {
                "id": "1-0",
                "values": {
                    "requestId": "req-1",
                    "groupIdx": "0",
                    "taskId": "g1-t1-security",
                    "status": "completed",
                    "resultKey": "cache:task:req-1:0:g1-t1-security:result",
                    "result": json.dumps({"score": 10}),
                }
            },
            {
                "id": "2-0",
                "values": {
                    "requestId": "req-1",
                    "groupIdx": "1",
                    "taskId": "g2-t1-schedule",
                    "status": "completed",
                    "resultKey": "cache:task:req-1:1:g2-t1-schedule:result",
                    "result": json.dumps({"score": 20}),
                }
            },
            {
                "id": "3-0",
                "values": {
                    "requestId": "req-1",
                    "groupIdx": "1",
                    "taskId": "g2-t2-analytics",
                    "status": "completed",
                    "resultKey": "cache:task:req-1:1:g2-t2-analytics:result",
                    "result": json.dumps({"score": 30}),
                }
            },
        ])

        event = {
            "requestId": "req-1",
            "xmlKey": "cache:request:req-1:xml",
            "responseKey": "cache:request:req-1:response",
        }

        response = self.orchestrator.run(event)

        self.assertEqual(response["responseKey"], "cache:request:req-1:response")
        response_xml = self.redis.get("cache:request:req-1:response")
        self.assertIn("<response", response_xml)
        self.assertIn("task", response_xml)
        self.assertTrue(self.redis.streams[TASK_DISPATCH_STREAM])
        lifecycle_events = self.redis.streams[REQUEST_LIFECYCLE_STREAM]
        statuses = [entry["values"]["status"] for entry in lifecycle_events]
        self.assertIn("started", statuses)
        self.assertIn("completed", statuses)

    def test_group_failure_raises(self):
        xml = """<req><project><group name='g1'>\n        <valuation name='security'><instrument ref-name='i1'/></valuation></group></project></req>"""
        self.redis.set("cache:request:req-2:xml", xml)
        self.redis.pending_updates.append({
            "id": "1-0",
            "values": {
                "requestId": "req-2",
                "groupIdx": "0",
                "taskId": "g1-t1-security",
                "status": "failed",
                "attempt": "3",
                "resultKey": "cache:task:req-2:0:g1-t1-security:result",
            }
        })
        event = {
            "requestId": "req-2",
            "xmlKey": "cache:request:req-2:xml",
        }
        with self.assertRaises(RuntimeError):
            self.orchestrator.run(event)


if __name__ == "__main__":
    unittest.main()
