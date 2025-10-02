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
  xreadgroupStream,
} from './utils.js';

/**
 * Consumes request ingestion stream and invokes vnvs Lambda per request.
 * Maintains consumer group for distributed processing and tracks request lifecycle.
 */
export class MainOrchestrator {
  constructor({ redis, redisRequestStream, stateRepository, lifecyclePublisher, requestInvoker, logger }) {
    if (!redis) {
      throw new Error('redis client is required');
    }
    this.redis = redis;
    this.redisRequestStream = redisRequestStream;
    this.stateRepository = stateRepository;
    this.lifecyclePublisher = lifecyclePublisher;
    this.requestInvoker = requestInvoker;
    this.logger = logger || console;
    this.stopped = false;
  }

  /**
   * Ensures request ingestion consumer group exists.
   * Creates the consumer group if it doesn't exist (ignores BUSYGROUP errors).
   */
  async ensureConsumerGroup() {
    try {
      if (typeof this.redis.xgroupCreate === 'function') {
        await this.redis.xgroupCreate(REQUEST_STREAM, REQUEST_CONSUMER_GROUP, '0', { MKSTREAM: true });
      } else if (typeof this.redis.xgroup === 'function') {
        await this.redis.xgroup('CREATE', REQUEST_STREAM, REQUEST_CONSUMER_GROUP, '0', 'MKSTREAM');
      } else if (typeof this.redis.call === 'function') {
        await this.redis.call('XGROUP', 'CREATE', REQUEST_STREAM, REQUEST_CONSUMER_GROUP, '0', 'MKSTREAM');
      } else {
        this.logger.warn?.('redis client cannot create consumer groups; skipping');
        return;
      }
    } catch (error) {
      if (!this._isBusyGroupError(error)) {
        throw error;
      }
    }
  }

  /**
   * Start polling request ingestion stream with consumer group.
   */
  async startPolling({ blockMs = DEFAULT_BLOCK_MS, maxBatchSize = 10 } = {}) {
    await this.ensureConsumerGroup();
    this.stopped = false;
    while (!this.stopped) {
      const response = await xreadgroupStream(this.redisRequestStream, {
        stream: REQUEST_STREAM,
        group: REQUEST_CONSUMER_GROUP,
        consumer: REQUEST_CONSUMER_NAME,
        count: maxBatchSize,
        block: blockMs,
      });
      if (!response || response.length === 0) {
        continue;
      }
      this.logger.info(`Got response.length=${response.length}`);
      for (const entry of response) {
        try {
          await this._processEntry(entry);
        } catch (error) {
          this.logger.error?.('Failed to process request entry', { entry, error });
        }
      }
    }
  }

  /**
   * Stops the polling loop.
   */
  stop() {
    this.stopped = true;
  }

  /**
   * Processes a single stream entry from request ingestion stream.
   * Initializes state, publishes lifecycle event, invokes vnvs Lambda, and acknowledges entry.
   */
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
    this.logger.info?.(`Acknowledge response REQUEST_STREAM=${REQUEST_STREAM}, REQUEST_CONSUMER_GROUP=${REQUEST_CONSUMER_GROUP}, entry.id=${entry.id}`);
    await this.redis.xack(REQUEST_STREAM, REQUEST_CONSUMER_GROUP, entry.id);
  }

  /**
   * Converts stream entry to request envelope object.
   */
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

  /**
   * Safely parses JSON string, logging warning on failure.
   */
  _safeJsonParse(value, field) {
    try {
      return JSON.parse(value);
    } catch (error) {
      this.logger.warn?.(`Unable to parse JSON field ${field}`, { error });
      return undefined;
    }
  }

  /**
   * Checks if error is a BUSYGROUP error (consumer group already exists).
   */
  _isBusyGroupError(error) {
    const message = error?.message || '';
    return message.includes('BUSYGROUP');
  }
}
