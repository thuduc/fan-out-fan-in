import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { Buffer } from 'node:buffer';

import { CurveBuildingService } from '../src/curveBuildingService.js';
import { createHttpApp } from '../src/httpApp.js';
import { DEFAULTS } from '../src/config.js';

class StubLambdaInvoker {
  constructor() {
    this.calls = [];
    this.nextResponse = { statusCode: 200, body: 'ok', contentType: 'text/plain' };
  }

  async invokeSync(payload) {
    this.calls.push(payload);
    return this.nextResponse;
  }
}

class StubRedisClient {
  async ping() {
    return 'PONG';
  }
  async hgetall() {
    return {};
  }
  async get() {
    return null;
  }
  async set() {
    return 'OK';
  }
  async exists() {
    return 1;
  }
  async hincrby() {
    return 1;
  }
  async hset() {
    return 1;
  }
  async xadd() {
    return '0-1';
  }
}

function createCurveService(invoker) {
  return new CurveBuildingService({
    lambdaInvoker: invoker,
    config: {
      curveLambdaName: 'glv-vnms',
      curveTimeoutMs: 500,
    },
    logger: createNullLogger(),
  });
}

test('CurveBuildingService returns lambda response body', async () => {
  const invoker = new StubLambdaInvoker();
  invoker.nextResponse = { statusCode: 200, body: 'sample curve', contentType: 'text/plain' };
  const service = createCurveService(invoker);

  const payloadBuffer = Buffer.from('sample curve');
  const result = await service.submitCurveRequest({
    buffer: payloadBuffer,
    contentType: 'text/plain',
  });

  assert.equal(result.statusCode, 200);
  assert.equal(result.body, 'sample curve');
  assert.equal(result.contentType, 'text/plain');

  assert.equal(invoker.calls.length, 1);
  assert.equal(invoker.calls[0].contentType, 'text/plain');
});

test('POST /curveBuildingRequest accepts text/plain and returns attachment on success', async (t) => {
  const invoker = new StubLambdaInvoker();
  const curveService = createCurveService(invoker);
  const redis = new StubRedisClient();
  const app = createHttpApp({ redis, config: DEFAULTS, logger: createNullLogger(), curveService });

  const server = http.createServer(app);
  t.after(() => {
    server.close();
  });
  server.listen(0);
  await once(server, 'listening');
  const { port } = server.address();

  invoker.nextResponse = { statusCode: 200, body: 'plain payload', contentType: 'text/plain' };

  const response = await fetch(`http://127.0.0.1:${port}/curveBuildingRequest`, {
    method: 'POST',
    headers: {
      'Content-Type': 'text/plain',
    },
    body: 'plain payload',
  });

  const text = await response.text();
  assert.equal(text, 'plain payload');
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('requeststatus'), 'success');
  assert.ok((response.headers.get('content-type') || '').includes('text/plain'));
  const disposition = response.headers.get('content-disposition');
  assert.ok(disposition && disposition.includes('finalResult.xml'));
  assert.equal(invoker.calls.length, 1);
  assert.equal(invoker.calls[0].bodyBase64, Buffer.from('plain payload', 'utf8').toString('base64'));
});

test('POST /curveBuildingRequest surfaces lambda error', async (t) => {
  const invoker = new StubLambdaInvoker();
  invoker.nextResponse = { statusCode: 400, body: 'invalid curve' };
  const curveService = createCurveService(invoker);
  const redis = new StubRedisClient();
  const app = createHttpApp({ redis, config: DEFAULTS, logger: createNullLogger(), curveService });

  const server = http.createServer(app);
  t.after(() => server.close());
  server.listen(0);
  await once(server, 'listening');
  const { port } = server.address();

  const response = await fetch(`http://127.0.0.1:${port}/curveBuildingRequest`, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain' },
    body: 'payload',
  });

  assert.equal(response.status, 400);
  assert.equal(response.headers.get('requeststatus'), 'failed');
  const body = await response.json();
  assert.deepEqual(body, { message: 'invalid curve' });
});

function createNullLogger() {
  return {
    info() {},
    warn() {},
    error() {},
    debug() {},
  };
}
