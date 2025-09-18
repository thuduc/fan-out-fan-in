export const DEFAULTS = {
  httpPort: Number(process.env.HTTP_PORT || 8080),
  payloadMaxBytes: parseSize(process.env.PAYLOAD_MAX_BYTES) || 1024 * 1024,
  requestTtlSeconds: Number(process.env.REQUEST_TTL_SECONDS || 86400),
  syncWaitTimeoutMs: Number(process.env.SYNC_WAIT_TIMEOUT_MS || 120000),
  lifecyclePollBlockMs: Number(process.env.LIFECYCLE_BLOCK_MS || 1000),
};

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
