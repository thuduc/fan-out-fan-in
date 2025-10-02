import { nowIso } from './utils.js';

/**
 * Manages request state in Redis (state:request:<requestId> hash).
 * Provides methods for initializing state, updating lifecycle, and tracking group progress.
 */
export class RequestStateRepository {
  constructor(redis) {
    this.redis = redis;
  }

  /**
   * Initializes request state hash in Redis.
   */
  async initializeRequest(envelope) {
    const { requestId, xmlKey, metadataKey, responseKey, groupCount, submittedAt } = envelope;
    const key = this._requestStateKey(requestId);
    const baseState = {
      status: 'received',
      xmlKey,
      responseKey: responseKey || '',
      metadataKey: metadataKey || '',
      groupCount: groupCount ?? 0,
      currentGroup: -1,
      retryCount: 0,
      receivedAt: nowIso(),
      submittedAt: submittedAt || '',
    };
    await this.redis.hset(key, baseState);
  }

  /**
   * Updates request lifecycle status and optional extra fields.
   */
  async markLifecycle(requestId, status, extra = {}) {
    const key = this._requestStateKey(requestId);
    await this.redis.hset(key, { status, ...extra });
  }

  /**
   * Sets the currently active group index.
   */
  async setActiveGroup(requestId, groupIndex) {
    const key = this._requestStateKey(requestId);
    await this.redis.hset(key, { currentGroup: groupIndex });
  }

  /**
   * Increments retry counter for request.
   */
  async incrementRetry(requestId) {
    const key = this._requestStateKey(requestId);
    await this.redis.hincrby(key, 'retryCount', 1);
  }

  /**
   * Persists metadata to Redis hash.
   */
  async persistMetadata(requestId, metadata) {
    if (!metadata) {
      return;
    }
    const key = `cache:request:${requestId}:metadata`;
    await this.redis.hset(key, metadata);
  }

  /**
   * Generates Redis key for request state hash.
   */
  _requestStateKey(requestId) {
    return `state:request:${requestId}`;
  }
}
