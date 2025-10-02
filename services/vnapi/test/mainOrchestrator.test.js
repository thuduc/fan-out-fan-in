import test from 'node:test';
import assert from 'node:assert/strict';
import Redis from 'ioredis';
import { MainOrchestrator } from '../src/mainOrchestrator.js';
import { RequestStateRepository } from '../src/requestStateRepository.js';
import { LifecyclePublisher } from '../src/lifecyclePublisher.js';
import { LIFECYCLE_STREAM, REQUEST_STREAM, REQUEST_CONSUMER_GROUP } from '../src/constants.js';

class FakeInvoker {
  constructor() {
    this.calls = [];
  }

  async invokeAsync(payload) {
    this.calls.push(payload);
  }
}

const REDIS_URL = resolveTestRedisUrl(12);

test('MainOrchestrator processes request entries and orchestrates invocation', { concurrency: false }, async (t) => {
  const redis = new Redis(REDIS_URL);
  t.after(() => redis.quit());
  await redis.flushdb();
  try {
    await redis.xgroup('CREATE', REQUEST_STREAM, REQUEST_CONSUMER_GROUP, '0', 'MKSTREAM');
  } catch (error) {
    if (!String(error).includes('BUSYGROUP')) {
      throw error;
    }
  }
  const stateRepository = new RequestStateRepository(redis);
  const lifecyclePublisher = new LifecyclePublisher(redis);
  const invoker = new FakeInvoker();
  const orchestrator = new MainOrchestrator({
    redis,
    stateRepository,
    lifecyclePublisher,
    requestInvoker: invoker,
    logger: { error: () => {}, warn: () => {} }
  });

  const entry = {
    id: '1-0',
    values: {
      requestId: 'req-123',
      xmlKey: 'cache:request:req-123:xml',
      groupCount: '2',
      metadata: JSON.stringify({ tenant: 'alpha', priority: 'high' }),
      submittedAt: '2024-01-01T00:00:00Z',
    }
  };

  await orchestrator._processEntry(entry);

  const state = await redis.hgetall('state:request:req-123');
  assert.equal(state.status, 'received');
  assert.equal(Number(state.groupCount), 2);

  const metadata = await redis.hgetall('cache:request:req-123:metadata');
  assert.equal(metadata.tenant, 'alpha');

  const lifecycleEntries = await redis.xrange(LIFECYCLE_STREAM, '-', '+');
  assert.equal(lifecycleEntries.length, 1, 'lifecycle event published');
  const event = arrayToObject(lifecycleEntries[0][1]);
  assert.equal(event.status, 'received');

  assert.equal(invoker.calls.length, 1, 'lambda invoked');
  assert.equal(invoker.calls[0].requestId, 'req-123');
  assert.equal(invoker.calls[0].xmlKey, 'cache:request:req-123:xml');
});

test('MainOrchestrator rejects invalid entries', { concurrency: false }, async (t) => {
  const redis = new Redis(REDIS_URL);
  t.after(() => redis.quit());
  await redis.flushdb();
  try {
    await redis.xgroup('CREATE', REQUEST_STREAM, REQUEST_CONSUMER_GROUP, '0', 'MKSTREAM');
  } catch (error) {
    if (!String(error).includes('BUSYGROUP')) {
      throw error;
    }
  }
  const stateRepository = new RequestStateRepository(redis);
  const lifecyclePublisher = new LifecyclePublisher(redis);
  const invoker = new FakeInvoker();
  const orchestrator = new MainOrchestrator({
    redis,
    stateRepository,
    lifecyclePublisher,
    requestInvoker: invoker,
    logger: { error: () => {}, warn: () => {} }
  });

  const invalidEntry = { id: '1-0', values: { xmlKey: 'x' } };
  await assert.rejects(() => orchestrator._processEntry(invalidEntry));
});

function arrayToObject(arr) {
  const result = {};
  for (let i = 0; i < arr.length; i += 2) {
    result[arr[i]] = arr[i + 1];
  }
  return result;
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
