import test from 'node:test';
import assert from 'node:assert/strict';
import Redis from 'ioredis';
import { RequestSubmissionService } from '../src/requestSubmissionService.js';
import { RequestQueryService } from '../src/requestQueryService.js';
import { REQUEST_STREAM, LIFECYCLE_STREAM } from '../src/constants.js';

const REDIS_URL = resolveTestRedisUrl(11);

test('RequestSubmissionService submit async stores XML and returns id', { concurrency: false }, async (t) => {
  const redis = new Redis(REDIS_URL);
  t.after(() => redis.quit());
  await redis.flushdb();
  const query = new RequestQueryService({ redis });
  const service = new RequestSubmissionService({ redis, config: { requestTtlSeconds: 60 }, queryService: query });
  const xmlPayload = '<req><project></project></req>';
  const result = await service.submit({ xml: xmlPayload, sync: false, metadata: { 'x-test': 'value' } });
  assert.equal(result.status, 'accepted');
  assert.ok(result.requestId);
  const xmlKey = `cache:request:${result.requestId}:xml`;
  const storedXml = await redis.get(xmlKey);
  assert.equal(storedXml, xmlPayload);
});

test('RequestSubmissionService submit sync resolves after lifecycle event', { concurrency: false }, async (t) => {
  const redis = new Redis(REDIS_URL);
  t.after(() => redis.quit());
  await redis.flushdb();
  const query = new RequestQueryService({ redis });
  const service = new RequestSubmissionService({
    redis,
    config: { syncWaitTimeoutMs: 2000, lifecyclePollBlockMs: 20, payloadVisibilityChecks: 0 },
    queryService: query,
  });

  const xmlPayload = '<req><project><group/></project></req>';
  service._awaitCompletion = async ({ requestId, responseKey }) => {
    await redis.set(responseKey, '<response/>');
    return { status: 'completed', responseXml: '<response/>' };
  };

  const submissionPromise = service.submit({ xml: xmlPayload, sync: true });

  const envelope = await waitFor(async () => readLatestStreamEntry(redis, REQUEST_STREAM), Boolean);
  const requestId = envelope.requestId;
  const responseKey = envelope.responseKey;

  const result = await Promise.race([
    submissionPromise,
    new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('submission timeout')), 3000);
      submissionPromise.then((value) => {
        clearTimeout(timeout);
        resolve(value);
      }, (error) => {
        clearTimeout(timeout);
        reject(error);
      });
    })
  ]);
  assert.equal(result.status, 'completed');
  assert.equal(result.responseXml, '<response/>');
});

test('QueryService returns structured status with numeric fields', { concurrency: false }, async (t) => {
  const redis = new Redis(REDIS_URL);
  t.after(() => redis.quit());
  await redis.flushdb();
  await redis.hset('state:request:req-123', {
    status: 'succeeded',
    groupCount: '3',
    currentGroup: '2',
  });
  const query = new RequestQueryService({ redis });
  const status = await query.getStatus('req-123');
  assert.equal(status.groupCount, 3);
  assert.equal(status.currentGroup, 2);
});

async function readLatestStreamEntry(redis, stream) {
  const entries = await redis.xrange(stream, '-', '+');
  if (!entries || entries.length === 0) {
    return null;
  }
  const [, fields] = entries.at(-1);
  return arrayToObject(fields);
}

function arrayToObject(arr) {
  const result = {};
  for (let i = 0; i < arr.length; i += 2) {
    result[arr[i]] = arr[i + 1];
  }
  return result;
}

async function waitFor(fetcher, predicate, { timeout = 5000, interval = 50 } = {}) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    last = await fetcher();
    if (predicate(last)) {
      return last;
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  throw new Error('Condition not met within timeout');
}

function resolveTestRedisUrl(db = 15) {
  const raw = process.env.REDIS_URL || 'redis://127.0.0.1:6379';
  try {
    const url = new URL(raw);
    url.pathname = `/${db}`;
    return url.toString();
  } catch {
    throw new Error(`Invalid REDIS_URL: ${raw}`);
  }
}
