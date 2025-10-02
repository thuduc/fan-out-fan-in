import { randomUUID } from 'node:crypto';
import {
  REQUEST_STREAM,
  LIFECYCLE_STREAM,
} from './constants.js';
import { nowIso, xadd, xreadStream, ensureKeyExists } from './utils.js';

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'completed', 'failed_terminal']);

/**
 * Handles XML request submission (sync and async) and lifecycle waiting.
 * Validates XML, generates request IDs, stores payloads in Redis cache,
 * and publishes to request ingestion stream.
 */
export class RequestSubmissionService {
  constructor({ redis, config, logger, queryService } = {}) {
    if (!redis) {
      throw new Error('redis client is required');
    }
    this.redis = redis;
    this.config = config || {};
    this.logger = logger || console;
    this.queryService = queryService;
  }

  /**
   * Submit XML valuation request (synchronous or asynchronous).
   */
  async submit({ xml, sync = false, metadata = {} }) {
    if (typeof xml !== 'string' || xml.trim().length === 0) {
      throw new Error('XML payload is required');
    }
    const requestId = randomUUID();
    const xmlKey = `cache:request:${requestId}:xml`;
    const responseKey = `cache:request:${requestId}:response`;

    await this._storeXml(xmlKey, xml);
    const ensured = await ensureKeyExists(this.redis, xmlKey, {
      attempts: this.config.payloadVisibilityChecks ?? 40,
      intervalMs: this.config.payloadVisibilityDelayMs ?? 25,
    });
    if (!ensured) {
      throw new Error(`Failed to verify XML payload visibility for ${xmlKey}`);
    }
    let metadataKey = '';
    if (metadata && Object.keys(metadata).length > 0) {
      metadataKey = `cache:request:${requestId}:metadata`;
      await this.redis.hset(metadataKey, flattenMetadata(metadata));
    }

    const groupCount = estimateGroupCount(xml);

    const envelope = {
      requestId,
      xmlKey,
      responseKey,
      submittedAt: nowIso(),
    };
    if (metadataKey) {
      envelope.metadataKey = metadataKey;
      envelope.metadata = JSON.stringify(metadata);
    }
    if (groupCount !== null) {
      envelope.groupCount = String(groupCount);
    }

    await xadd(this.redis, REQUEST_STREAM, '*', envelope);

    if (!sync) {
      return { requestId, status: 'accepted' };
    }

    const waitResult = await this._awaitCompletion({ requestId, responseKey });
    return { requestId, ...waitResult };
  }

  /**
   * Stores XML payload in Redis cache with optional TTL.
   */
  async _storeXml(key, value) {
    const ttl = this.config.requestTtlSeconds;
    if (ttl) {
      return this.redis.set(key, value, 'EX', ttl);
    }
    return this.redis.set(key, value);
  }

  /**
   * Blocks until request reaches terminal state (completed/failed) or timeout.
   * Polls lifecycle stream for status updates matching the given requestId.
   */
  async _awaitCompletion({ requestId, responseKey }) {
    // quick check for already-completed requests
    if (this.queryService) {
      const existing = await this.queryService.getResult(requestId);
      if (existing) {
        console.log(`Synchronous request ${requestId} already has response, returning immediately`);
        return { status: 'completed', responseXml: existing };
      }
      const status = await this.queryService.getStatus(requestId);
      if (status && TERMINAL_STATUSES.has(status.status)) {
        if (status.status.startsWith('failed')) {
          return { status: 'failed' };
        }
      }
    }

    const timeoutAt = Date.now() + (this.config.syncWaitTimeoutMs || 120000);
    let lastId = await this._resolveInitialLifecycleId();

    while (Date.now() < timeoutAt) {
      const remaining = Math.max(1, timeoutAt - Date.now());
      const blockMs = Math.min(this.config.lifecyclePollBlockMs || 1000, remaining);
      const entries = await xreadStream(this.redis, {
        stream: LIFECYCLE_STREAM,
        id: lastId,
        block: blockMs,
        count: 10,
      });
      if (!entries || entries.length === 0) {
        continue;
      }
      for (const entry of entries) {
        lastId = entry.id;
        const payload = entry.values || {};
        if (payload.requestId !== requestId) {
          continue;
        }
        const status = payload.status;
        if (status && TERMINAL_STATUSES.has(status)) {
          if (status.startsWith('failed')) {
            return { status: 'failed' };
          }
          const responseXml = await this.redis.get(responseKey);
          // console.log(`Synchronous request ${requestId} finished with status ${status}, responseKey=${responseKey}, responseXml length=${responseXml?.length}`);
          return { status: 'completed', responseXml: responseXml || '' };
        }
      }
    }

    return { status: 'pending' };
  }

  /**
   * Sleeps for specified milliseconds.
   */
  async _sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  /**
   * Resolves the initial stream ID for lifecycle polling.
   * Returns '$' to start reading only new entries.
   */
  async _resolveInitialLifecycleId() {
    return '$';
  }
}

/**
 * Estimates number of groups in XML by counting <group> tags.
 */
function estimateGroupCount(xml) {
  if (typeof xml !== 'string') {
    return null;
  }
  const matches = xml.match(/<group\b/gi);
  if (!matches) {
    return null;
  }
  return matches.length;
}

/**
 * Flattens metadata object to Redis hash format (all string values).
 */
function flattenMetadata(metadata) {
  const flattened = {};
  for (const [key, value] of Object.entries(metadata)) {
    flattened[key] = typeof value === 'object' ? JSON.stringify(value) : String(value);
  }
  return flattened;
}
