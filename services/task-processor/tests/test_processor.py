import os
import unittest

from redis import Redis
from lxml import etree

from app.processor import TaskProcessor
from app.constants import TASK_UPDATES_STREAM

REDIS_URL = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/13')


def create_redis():
    return Redis.from_url(REDIS_URL, decode_responses=True)


def fields_to_dict(entry):
    _, values = entry
    return {values[i]: values[i + 1] for i in range(0, len(values), 2)}


class TaskProcessorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.redis = create_redis()
        self.redis.flushdb()
        self.processor = TaskProcessor(self.redis)

    def tearDown(self):
        try:
            self.redis.flushdb()
        finally:
            self.redis.close()

    def test_successful_processing(self):
        payload_key = 'cache:task:req-1:0:g1-t1-security:xml'
        result_key = 'cache:task:req-1:0:g1-t1-security:result'
        xml = (
            "<taskRequest><context/><valuation name='security'>"
            "<instrument ref-name='i1'/><analytics><price><amount>2</amount></price></analytics>"
            "</valuation></taskRequest>"
        )
        self.redis.set(payload_key, xml)

        entry = {
            'values': {
                'requestId': 'req-1',
                'groupIdx': '0',
                'groupName': 'Group1',
                'taskId': 'g1-t1-security',
                'valuationName': 'security',
                'payloadKey': payload_key,
                'resultKey': result_key,
                'attempt': '1',
            }
        }

        result = self.processor.handle_dispatch(entry)

        stored_xml = self.redis.get(result_key)
        self.assertIsNotNone(stored_xml)
        document = etree.fromstring(stored_xml.encode('utf-8'))
        valuation = document.find('.//valuation')
        self.assertIsNotNone(valuation)
        self.assertEqual(valuation.get('name'), 'security')
        amount = document.find('.//analytics/price/amount')
        self.assertIsNotNone(amount)
        generated_value = float(amount.text)
        self.assertGreaterEqual(generated_value, 50.0)
        self.assertLessEqual(generated_value, 100.0)

        updates = self.redis.xrange(TASK_UPDATES_STREAM, '-', '+')
        update = fields_to_dict(updates[0])
        self.assertEqual(update['requestId'], 'req-1')
        self.assertEqual(result['status'], 'completed')

    def test_missing_payload_raises_and_notifies(self):
        entry = {
            'values': {
                'requestId': 'req-2',
                'groupIdx': '0',
                'groupName': 'Group1',
                'taskId': 'g1-t1',
                'payloadKey': 'cache:task:req-2:0:g1-t1:xml',
                'resultKey': 'cache:task:req-2:0:g1-t1:result',
                'attempt': '1',
            }
        }

        with self.assertRaises(FileNotFoundError):
            self.processor.handle_dispatch(entry)

        updates = self.redis.xrange(TASK_UPDATES_STREAM, '-', '+')
        update = fields_to_dict(updates[0])
        self.assertEqual(update['status'], 'failed')
        failure = self.redis.get('cache:request:req-2:failure')
        self.assertIsNotNone(failure)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
