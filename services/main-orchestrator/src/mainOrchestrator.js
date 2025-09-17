import {
  REQUEST_STREAM,
  REQUEST_CONSUMER_GROUP,
  REQUEST_CONSUMER_NAME,
  DEFAULT_BLOCK_MS,
} from './constants.js';
import {
  ensureString,
  ensureNumber,
  generateExecutionToken,
} from './utils.js';

export class MainOrchestrator {
  constructor({ redis, stateRepository, lifecyclePublisher, requestInvoker, logger }) {
    if (!redis) {
      throw new Error('redis client is required');
    }
    this.redis = redis;
    this.stateRepository = stateRepository;
    this.lifecyclePublisher = lifecyclePublisher;
    this.requestInvoker = requestInvoker;
    this.logger = logger || console;
    this.stopped = false;
  }

  async ensureConsumerGroup() {
    if (typeof this.redis.xgroupCreate !== 'function') {
      this.logger.warn?.('redis client does not support xgroupCreate; skipping group creation');
      return;
    }
    try {
      await this.redis.xgroupCreate(REQUEST_STREAM, REQUEST_CONSUMER_GROUP, '0', { MKSTREAM: true });
    } catch (error) {
      if (!this._isBusyGroupError(error)) {
        throw error;
      }
    }
  }

  async startPolling({ blockMs = DEFAULT_BLOCK_MS, maxBatchSize = 10 } = {}) {
    await this.ensureConsumerGroup();
    this.stopped = false;
    while (!this.stopped) {
      const response = await this.redis.xreadgroup({
        stream: REQUEST_STREAM,
        group: REQUEST_CONSUMER_GROUP,
        consumer: REQUEST_CONSUMER_NAME,
        count: maxBatchSize,
        block: blockMs,
      });
      if (!response || response.length === 0) {
        continue;
      }
      for (const entry of response) {
        try {
          await this._processEntry(entry);
        } catch (error) {
          this.logger.error?.('Failed to process request entry', { entry, error });
        }
      }
    }
  }

  stop() {
    this.stopped = true;
  }

  async _processEntry(entry) {
    const envelope = this._toEnvelope(entry);
    const metadata = envelope.metadata;
    if (metadata) {
      await this.stateRepository.persistMetadata(envelope.requestId, metadata);
    }
    await this.stateRepository.initializeRequest(envelope);
    await this.lifecyclePublisher.publish(envelope.requestId, 'received', {
      requestId: envelope.requestId,
      xmlKey: envelope.xmlKey,
      groupCount: envelope.groupCount,
    });
    await this.requestInvoker.invokeAsync({
      requestId: envelope.requestId,
      xmlKey: envelope.xmlKey,
      metadataKey: envelope.metadataKey,
      responseKey: envelope.responseKey,
      groupCount: envelope.groupCount,
      executionToken: generateExecutionToken(),
    });
    await this.redis.xack(REQUEST_STREAM, REQUEST_CONSUMER_GROUP, entry.id);
  }

  _toEnvelope(entry) {
    const values = entry.values || entry.fields || {};
    const requestId = ensureString(values.requestId, 'requestId');
    const xmlKey = ensureString(values.xmlKey, 'xmlKey');
    const groupCount = values.groupCount !== undefined ? ensureNumber(values.groupCount, 'groupCount') : 0;
    const metadataKey = values.metadataKey || '';
    const responseKey = values.responseKey || `cache:request:${requestId}:response`;
    const submittedAt = values.submittedAt || values.createdAt || '';
    let metadata;
    if (values.metadata) {
      metadata = this._safeJsonParse(values.metadata, 'metadata');
    }
    return {
      requestId,
      xmlKey,
      metadataKey,
      responseKey,
      submittedAt,
      groupCount,
      metadata,
    };
  }

  _safeJsonParse(value, field) {
    try {
      return JSON.parse(value);
    } catch (error) {
      this.logger.warn?.(`Unable to parse JSON field ${field}`, { error });
      return undefined;
    }
  }

  _isBusyGroupError(error) {
    const message = error?.message || '';
    return message.includes('BUSYGROUP');
  }
}
