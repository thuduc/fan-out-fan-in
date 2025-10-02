import { LIFECYCLE_STREAM } from './constants.js';
import { nowIso, serializeLifecycleEvent, xadd } from './utils.js';

/**
 * Publishes request lifecycle events to stream:request:lifecycle.
 * Multi-subscriber stream for tracking request state changes across services.
 */
export class LifecyclePublisher {
  constructor(redis) {
    this.redis = redis;
  }

  /**
   * Publishes a lifecycle event to the lifecycle stream.
   */
  async publish(requestId, status, details = {}) {
    const event = {
      requestId,
      status,
      at: nowIso(),
      ...details,
    };
    const payload = serializeLifecycleEvent(event);
    await xadd(this.redis, LIFECYCLE_STREAM, '*', payload);
  }
}
