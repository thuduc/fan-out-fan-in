import { LIFECYCLE_STREAM } from './constants.js';
import { nowIso, serializeLifecycleEvent } from './utils.js';

export class LifecyclePublisher {
  constructor(redis) {
    this.redis = redis;
  }

  async publish(requestId, status, details = {}) {
    const event = {
      requestId,
      status,
      at: nowIso(),
      ...details,
    };
    const payload = serializeLifecycleEvent(event);
    await this.redis.xadd(LIFECYCLE_STREAM, '*', payload);
  }
}
