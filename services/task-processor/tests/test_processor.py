import json
import unittest

from app.processor import TaskProcessor
from app.constants import TASK_UPDATES_STREAM


class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.streams = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value):
        self.kv[key] = value

    def xadd(self, stream, _id, values):
        entries = self.streams.setdefault(stream, [])
        entry_id = f"{len(entries) + 1}-0"
        entries.append({"id": entry_id, "values": values})
        return entry_id


class TaskProcessorTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.processor = TaskProcessor(self.redis)

    def test_successful_processing(self):
        payload_key = "cache:task:req-1:0:g1-t1-security:xml"
        result_key = "cache:task:req-1:0:g1-t1-security:result"
        xml = """<taskRequest><context/><valuation name='security'>\n        <instrument ref-name='i1'/><analytics><price><amount>2</amount></price></analytics>\n        </valuation></taskRequest>"""
        self.redis.set(payload_key, xml)
        entry = {
            "values": {
                "requestId": "req-1",
                "groupIdx": "0",
                "groupName": "Group1",
                "taskId": "g1-t1-security",
                "valuationName": "security",
                "payloadKey": payload_key,
                "resultKey": result_key,
                "attempt": "1",
            }
        }

        result = self.processor.handle_dispatch(entry)
        stored = json.loads(self.redis.get(result_key))
        self.assertEqual(stored["valuation"], "security")
        self.assertEqual(stored["instrument"], "i1")
        updates = self.redis.streams[TASK_UPDATES_STREAM]
        self.assertEqual(updates[0]["values"]["status"], "completed")
        self.assertEqual(result["status"], "completed")

    def test_missing_payload_raises_and_notifies(self):
        payload_key = "cache:task:req-2:0:g1-t1:xml"
        result_key = "cache:task:req-2:0:g1-t1:result"
        entry = {
            "values": {
                "requestId": "req-2",
                "groupIdx": "0",
                "taskId": "g1-t1",
                "payloadKey": payload_key,
                "resultKey": result_key,
                "attempt": "1",
            }
        }
        with self.assertRaises(FileNotFoundError):
            self.processor.handle_dispatch(entry)
        updates = self.redis.streams[TASK_UPDATES_STREAM]
        self.assertEqual(updates[0]["values"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
