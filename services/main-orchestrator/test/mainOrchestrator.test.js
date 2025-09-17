import test from 'node:test';
import assert from 'node:assert/strict';
import { MainOrchestrator } from '../src/mainOrchestrator.js';
import { RequestStateRepository } from '../src/requestStateRepository.js';
import { LifecyclePublisher } from '../src/lifecyclePublisher.js';
import { LIFECYCLE_STREAM } from '../src/constants.js';

class FakeRedis {
  constructor() {
    this.hashes = new Map();
    this.streams = new Map();
    this.acks = [];
  }

  async hset(key, values) {
    const current = this.hashes.get(key) || {};
    this.hashes.set(key, { ...current, ...values });
  }

  async hincrby(key, field, increment) {
    const current = this.hashes.get(key) || {};
    const base = Number(current[field] || 0);
    current[field] = base + increment;
    this.hashes.set(key, current);
    return current[field];
  }

  async xadd(stream, id, values) {
    const entries = this.streams.get(stream) || [];
    entries.push({ id: id === '*' ? `${entries.length + 1}-0` : id, values });
    this.streams.set(stream, entries);
    return entries.at(-1).id;
  }

  async xack(stream, group, id) {
    this.acks.push({ stream, group, id });
  }

  async xgroupCreate() {
    // No-op for fake.
  }

  async xreadgroup() {
    return [];
  }
}

class FakeInvoker {
  constructor() {
    this.calls = [];
  }

  async invokeAsync(payload) {
    this.calls.push(payload);
  }
}

test('MainOrchestrator processes request entries and orchestrates invocation', async () => {
  const redis = new FakeRedis();
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

  const stateKey = 'state:request:req-123';
  assert.equal(redis.hashes.has(stateKey), true, 'state stored');
  assert.equal(redis.hashes.get(stateKey).status, 'received');
  assert.equal(redis.hashes.get(stateKey).groupCount, 2);

  const metadataKey = 'cache:request:req-123:metadata';
  assert.equal(redis.hashes.get(metadataKey).tenant, 'alpha');

  const lifecycleEntries = redis.streams.get(LIFECYCLE_STREAM) || [];
  assert.equal(lifecycleEntries.length, 1, 'lifecycle event published');
  assert.equal(lifecycleEntries[0].values.status, 'received');

  assert.equal(invoker.calls.length, 1, 'lambda invoked');
  assert.equal(invoker.calls[0].requestId, 'req-123');
  assert.equal(invoker.calls[0].xmlKey, 'cache:request:req-123:xml');

  assert.equal(redis.acks.length, 1, 'entry acknowledged');
  assert.equal(redis.acks[0].id, '1-0');
});

test('MainOrchestrator rejects invalid entries', async () => {
  const redis = new FakeRedis();
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
