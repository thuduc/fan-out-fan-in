import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, access } from 'node:fs/promises';
import { constants as fsConstants } from 'node:fs';
import http from 'node:http';
import { once } from 'node:events';

import { CurveBuildingService } from '../src/curveBuildingService.js';
import { createHttpApp } from '../src/httpApp.js';
import { DEFAULTS } from '../src/config.js';

class StubLambdaInvoker {
  constructor() {
    this.calls = [];
  }

  async invokeSync(payload) {
    this.calls.push(payload);
    const body = payload.bodyBase64 ? Buffer.from(payload.bodyBase64, 'base64') : Buffer.alloc(0);
    return {
      body,
      contentType: 'text/plain',
    };
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

test('CurveBuildingService writes input and output artifacts', async (t) => {
  const invoker = new StubLambdaInvoker();
  const service = createCurveService(invoker);

  const payloadBuffer = Buffer.from('sample curve');
  const result = await service.submitCurveRequest({
    buffer: payloadBuffer,
    contentType: 'text/plain',
    originalName: 'input.mkt',
  });

  t.after(async () => {
    await result.cleanup().catch(() => {});
  });

  const storedInput = await readFile(result.inputPath, 'utf8');
  assert.equal(storedInput, 'sample curve');

  const storedOutput = await readFile(result.outputPath, 'utf8');
  assert.equal(storedOutput, 'sample curve');

  assert.equal(invoker.calls.length, 1);
  assert.equal(invoker.calls[0].filename, 'input.mkt');

  await result.cleanup();
  await assert.rejects(() => access(result.inputPath, fsConstants.F_OK), /ENOENT/);
});

test('POST /curveBuildingRequest accepts text/plain', async (t) => {
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
  assert.equal(response.headers.get('content-type'), 'text/plain');
  const disposition = response.headers.get('content-disposition');
  assert.ok(disposition && disposition.includes('finalResult.xml'));
  assert.equal(invoker.calls.length, 1);
  assert.equal(invoker.calls[0].bodyBase64, Buffer.from('plain payload', 'utf8').toString('base64'));
});

test('POST /curveBuildingRequest accepts multipart/form-data', async (t) => {
  const invoker = new StubLambdaInvoker();
  const curveService = createCurveService(invoker);
  const redis = new StubRedisClient();
  const app = createHttpApp({ redis, config: DEFAULTS, logger: createNullLogger(), curveService });

  const server = http.createServer(app);
  t.after(() => server.close());
  server.listen(0);
  await once(server, 'listening');
  const { port } = server.address();

  const form = new FormData();
  form.append('file', new Blob(['multipart payload'], { type: 'text/plain' }), 'input.mkt');

  const response = await fetch(`http://127.0.0.1:${port}/curveBuildingRequest`, {
    method: 'POST',
    body: form,
  });

  const text = await response.text();
  assert.equal(text, 'multipart payload');
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('requeststatus'), 'success');
  assert.equal(invoker.calls.length, 1);
});

function createNullLogger() {
  return {
    info() {},
    warn() {},
    error() {},
    debug() {},
  };
}
