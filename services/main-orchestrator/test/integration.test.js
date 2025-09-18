import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import Redis from 'ioredis';

import {
  MainOrchestrator,
  RequestStateRepository,
  LifecyclePublisher,
  RequestSubmissionService,
  RequestQueryService,
} from '../src/index.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, '..', '..', '..');

const REQUEST_PYTHON = resolvePythonExecutable('request-orchestrator');
const TASK_PYTHON = resolvePythonExecutable('task-processor');
const REQUEST_RUNNER = path.join(repoRoot, 'services/request-orchestrator/app/local_runner.py');
const TASK_WORKER = path.join(repoRoot, 'services/task-processor/app/worker_loop.py');
const REQUEST_XML_PATH = path.join(repoRoot, 'request.xml');

const REDIS_URL = resolveTestRedisUrl(15);

class LocalRequestInvoker {
  constructor({ redisUrl }) {
    this.redisUrl = redisUrl;
  }

  async invokeAsync(payload) {
    if (payload?.xmlKey) {
      const redis = new Redis(this.redisUrl);
      try {
        const xmlValue = await waitFor(
          async () => redis.get(payload.xmlKey),
          (value) => typeof value === 'string' && value.length > 0,
          { timeout: 5000, interval: 50 }
        );
        if (xmlValue == null) {
          throw new Error(`XML payload missing for key ${payload.xmlKey}`);
        }
      } finally {
        await redis.quit().catch(() => {});
      }
    }
    await runPython(REQUEST_PYTHON, [REQUEST_RUNNER, JSON.stringify(payload), this.redisUrl]);
  }
}

test('integration: async submission resolves downstream state', { concurrency: false }, async (t) => {
  const harness = await createHarness(t);
  const { submissionService, queryService, xml } = harness;

  const submission = await submissionService.submit({ xml, sync: false });
  assert.equal(submission.status, 'accepted');
  assert.ok(submission.requestId);

  const status = await waitFor(
    async () => queryService.getStatus(submission.requestId),
    (state) => state && ['succeeded', 'completed'].includes(state.status),
    { timeout: 25000, interval: 200 }
  );
  assert.equal(status.requestId, submission.requestId);
  assert.ok(['succeeded', 'completed'].includes(status.status));

  const responseXml = await waitFor(async () => queryService.getResult(submission.requestId), Boolean);
  assert.ok(responseXml.includes('<response'));
});

test('integration: sync submission returns composed response', { concurrency: false }, async (t) => {
  const harness = await createHarness(t);
  const { submissionService, queryService, xml } = harness;

  const result = await submissionService.submit({ xml, sync: true });
  assert.equal(result.status, 'completed');
  assert.ok(result.responseXml.includes('<response'));

  const status = await queryService.getStatus(result.requestId);
  assert.equal(status.status, 'succeeded');
});

async function createHarness(t) {
  const xml = await readFile(REQUEST_XML_PATH, 'utf8');

  const redisMain = new Redis(REDIS_URL, { lazyConnect: false });
  const redisSubmit = new Redis(REDIS_URL, { lazyConnect: false });
  const redisQuery = new Redis(REDIS_URL, { lazyConnect: false });

  await redisMain.flushdb();

  const taskWorker = spawnPython(TASK_PYTHON, [TASK_WORKER, REDIS_URL]);

  const logger = createNullLogger();
  const requestInvoker = new LocalRequestInvoker({ redisUrl: REDIS_URL });
  const stateRepository = new RequestStateRepository(redisMain);
  const lifecyclePublisher = new LifecyclePublisher(redisMain);
  const orchestrator = new MainOrchestrator({
    redis: redisMain,
    stateRepository,
    lifecyclePublisher,
    requestInvoker,
    logger,
  });

  const pollPromise = orchestrator
    .startPolling({ blockMs: 50, maxBatchSize: 5 })
    .catch((error) => {
      logger.error?.('Orchestrator loop failed', { error });
    });

  const queryService = new RequestQueryService({ redis: redisQuery, logger });
  const submissionService = new RequestSubmissionService({
    redis: redisSubmit,
    config: {
      syncWaitTimeoutMs: 15000,
      lifecyclePollBlockMs: 250,
    },
    logger,
    queryService,
  });

  t.after(async () => {
    orchestrator.stop();
    await pollPromise;
    await Promise.all([
      cleanupRedis(redisMain),
      cleanupRedis(redisSubmit),
      cleanupRedis(redisQuery),
    ]);
    await stopProcess(taskWorker);
  });

  return { submissionService, queryService, xml };
}

function resolvePythonExecutable(serviceDir) {
  const base = path.join(repoRoot, 'services', serviceDir, 'venv');
  const bin = process.platform === 'win32' ? 'Scripts' : 'bin';
  const exe = process.platform === 'win32' ? 'python.exe' : 'python';
  return path.join(base, bin, exe);
}

function spawnPython(pythonPath, args) {
  const child = spawn(pythonPath, args, { stdio: 'inherit' });
  child.on('error', (error) => {
    throw error;
  });
  return child;
}

async function runPython(pythonPath, args) {
  await new Promise((resolve, reject) => {
    const child = spawn(pythonPath, args, { stdio: 'inherit' });
    child.once('error', reject);
    child.once('exit', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`Python process exited with code ${code}`));
      }
    });
  });
}

async function stopProcess(child) {
  if (!child || child.killed) {
    return;
  }
  const exitPromise = new Promise((resolve) => {
    child.once('exit', () => resolve());
  });
  child.kill('SIGINT');
  const timeout = setTimeout(() => {
    if (!child.killed) {
      child.kill('SIGKILL');
    }
  }, 2000);
  await exitPromise;
  clearTimeout(timeout);
}

async function cleanupRedis(client) {
  try {
    await client.quit();
  } catch {
    // ignore
  }
}

function createNullLogger() {
  return {
    info: () => {},
    warn: () => {},
    error: () => {},
    debug: () => {},
    trace: () => {},
  };
}

async function waitFor(fetcher, predicate, { timeout = 20000, interval = 250 } = {}) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    last = await fetcher();
    if (predicate(last)) {
      return last;
    }
    await delay(interval);
  }
  throw new Error(`Condition not met within ${timeout}ms; last value: ${JSON.stringify(last)}`);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
