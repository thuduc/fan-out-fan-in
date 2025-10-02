/**
 * Default configuration values loaded from environment variables.
 */
export const DEFAULTS = {
  /** HTTP server port (default: 8080) */
  httpPort: Number(process.env.HTTP_PORT || 8080),
  /** Max XML payload size in bytes (default: 10MB) */
  payloadMaxBytes: parseSize(process.env.PAYLOAD_MAX_BYTES) || 10 * 1024 * 1024,
  /** TTL for Redis cache keys in seconds (default: 86400 = 24 hours) */
  requestTtlSeconds: Number(process.env.REQUEST_TTL_SECONDS || 86400),
  /** Synchronous request wait timeout in milliseconds (default: 120000 = 2 minutes) */
  syncWaitTimeoutMs: Number(process.env.SYNC_WAIT_TIMEOUT_MS || 120000),
  /** Lifecycle stream polling block duration in milliseconds (default: 1000) */
  lifecyclePollBlockMs: Number(process.env.LIFECYCLE_BLOCK_MS || 1000),
};

/**
 * Parses size string to bytes (supports kb, mb, gb units).
 */
function parseSize(value) {
  if (!value) {
    return undefined;
  }
  if (/^[0-9]+$/.test(value)) {
    return Number(value);
  }
  const match = /^([0-9]+)(kb|mb|gb)$/i.exec(value);
  if (!match) {
    return undefined;
  }
  const size = Number(match[1]);
  const unit = match[2].toLowerCase();
  switch (unit) {
    case 'kb':
      return size * 1024;
    case 'mb':
      return size * 1024 * 1024;
    case 'gb':
      return size * 1024 * 1024 * 1024;
    default:
      return undefined;
  }
}
