/**
 * Redis stream for incoming valuation requests.
 * Consumed by MainOrchestrator via consumer group.
 */
export const REQUEST_STREAM = 'stream:request:ingest';

/**
 * Consumer group name for request ingestion stream.
 */
export const REQUEST_CONSUMER_GROUP = 'orchestrator-main';

/**
 * Consumer instance name for request ingestion stream.
 */
export const REQUEST_CONSUMER_NAME = 'orchestrator-main-1';

/**
 * Redis stream for request lifecycle events (multi-subscriber).
 */
export const LIFECYCLE_STREAM = 'stream:request:lifecycle';

/**
 * Default XREADGROUP block duration in milliseconds.
 */
export const DEFAULT_BLOCK_MS = 5000;

/**
 * Lambda async invocation timeout in milliseconds.
 */
export const INVOCATION_TIMEOUT_MS = 250; // asynchronous invoke, small timeout
