import { nowIso } from './utils.js';

export class RequestStateRepository {
  constructor(redis) {
    this.redis = redis;
  }

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

  async markLifecycle(requestId, status, extra = {}) {
    const key = this._requestStateKey(requestId);
    await this.redis.hset(key, { status, ...extra });
  }

  async setActiveGroup(requestId, groupIndex) {
    const key = this._requestStateKey(requestId);
    await this.redis.hset(key, { currentGroup: groupIndex });
  }

  async incrementRetry(requestId) {
    const key = this._requestStateKey(requestId);
    await this.redis.hincrby(key, 'retryCount', 1);
  }

  async persistMetadata(requestId, metadata) {
    if (!metadata) {
      return;
    }
    const key = `cache:request:${requestId}:metadata`;
    await this.redis.hset(key, metadata);
  }

  _requestStateKey(requestId) {
    return `state:request:${requestId}`;
  }
}
