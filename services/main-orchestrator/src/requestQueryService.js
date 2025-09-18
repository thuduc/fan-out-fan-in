function resolveMethod(redis, candidates) {
  for (const name of candidates) {
    const method = redis[name];
    if (typeof method === 'function') {
      return method.bind(redis);
    }
  }
  return null;
}

export class RequestQueryService {
  constructor({ redis, logger } = {}) {
    if (!redis) {
      throw new Error('redis client is required');
    }
    this.redis = redis;
    this.logger = logger || console;
    this.hgetall = resolveMethod(redis, ['hgetall', 'HGETALL']);
    this.getter = resolveMethod(redis, ['get', 'GET']);
  }

  async getStatus(requestId) {
    if (!this.hgetall) {
      throw new Error('redis client missing hgetall');
    }
    const key = `state:request:${requestId}`;
    const data = await this.hgetall(key);
    if (!data || Object.keys(data).length === 0) {
      return null;
    }
    return normalizeState(requestId, data);
  }

  async getResult(requestId) {
    if (!this.getter) {
      throw new Error('redis client missing get');
    }
    const key = `cache:request:${requestId}:response`;
    return await this.getter(key);
  }

  async getFailure(requestId) {
    if (!this.getter) {
      throw new Error('redis client missing get');
    }
    const key = `cache:request:${requestId}:failure`;
    return await this.getter(key);
  }
}

function normalizeState(requestId, data) {
  const normalized = { requestId };
  for (const [key, value] of Object.entries(data)) {
    if (value === undefined || value === null) {
      continue;
    }
    if (['groupCount', 'currentGroup', 'retryCount'].includes(key)) {
      const numeric = Number(value);
      normalized[key] = Number.isNaN(numeric) ? value : numeric;
    } else {
      normalized[key] = value;
    }
  }
  if (!normalized.status && normalized.completedAt) {
    normalized.status = 'succeeded';
  }
  return normalized;
}
