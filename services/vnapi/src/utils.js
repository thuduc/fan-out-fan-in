import crypto from 'crypto';

/**
 * Returns current timestamp in ISO 8601 format.
 */
export function nowIso() {
  return new Date().toISOString();
}

/**
 * Validates and returns string field, throwing if invalid.
 */
export function ensureString(value, field) {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`Missing or invalid field: ${field}`);
  }
  return value;
}

/**
 * Validates and returns numeric field, throwing if invalid.
 */
export function ensureNumber(value, field) {
  const asNumber = Number(value);
  if (!Number.isFinite(asNumber)) {
    throw new Error(`Missing or invalid numeric field: ${field}`);
  }
  return asNumber;
}

/**
 * Generates a unique execution token (UUID v4).
 */
export function generateExecutionToken() {
  return crypto.randomUUID();
}

/**
 * Serializes lifecycle event object to Redis stream format (all string values).
 */
export function serializeLifecycleEvent(event) {
  const serialized = {};
  for (const [key, value] of Object.entries(event)) {
    if (value === undefined || value === null) {
      continue;
    }
    serialized[key] = typeof value === 'object' ? JSON.stringify(value) : String(value);
  }
  return serialized;
}

/**
 * Converts object to flat array for Redis XADD command.
 */
export function toStreamEntries(values) {
  if (!values || typeof values !== 'object') {
    return [];
  }
  const entries = [];
  for (const [field, value] of Object.entries(values)) {
    if (value === undefined || value === null) {
      continue;
    }
    entries.push(field);
    entries.push(typeof value === 'object' ? JSON.stringify(value) : String(value));
  }
  return entries;
}

/**
 * Adds entry to Redis stream using XADD command.
 */
export function xadd(redis, stream, id, values) {
  if (!redis?.xadd) {
    throw new Error('redis client is missing xadd');
  }
  if (Array.isArray(values)) {
    return redis.xadd(stream, id, ...values);
  }
  const entries = toStreamEntries(values);
  return redis.xadd(stream, id, ...entries);
}

/**
 * Converts flat Redis stream fields array to object.
 */
export function fieldsArrayToObject(fields) {
  if (!fields) {
    return {};
  }
  if (!Array.isArray(fields)) {
    return { ...fields };
  }
  const result = {};
  for (let i = 0; i < fields.length; i += 2) {
    const field = fields[i];
    const value = fields[i + 1];
    if (field === undefined) {
      continue;
    }
    result[field] = value;
  }
  return result;
}

/**
 * Normalizes Redis XREAD/XREADGROUP response to consistent format.
 * Handles various Redis client response formats and converts to {id, values} objects.
 */
export function normalizeStreamEntries(raw) {
  if (!raw) {
    return [];
  }
  const container = Array.isArray(raw) ? raw : [raw];
  const entries = [];
  for (const item of container) {
    if (!item) {
      continue;
    }
    if (Array.isArray(item)) {
      const [, data] = item;
      const records = data || [];
      for (const record of records) {
        if (Array.isArray(record)) {
          const [id, fields] = record;
          entries.push({ id, values: fieldsArrayToObject(fields) });
        } else if (record && record.length === 2) {
          const [id, fields] = record;
          entries.push({ id, values: fieldsArrayToObject(fields) });
        } else if (record && record.id) {
          entries.push(record);
        }
      }
    } else if (item && item.id) {
      entries.push(item);
    }
  }
  return entries;
}

/**
 * Reads entries from Redis stream using XREAD (non-consumer-group).
 */
export async function xreadStream(redis, { stream, id = '$', block, count = 10 }) {
  const args = [];
  if (typeof block === 'number') {
    args.push('BLOCK', Math.max(0, block));
  }
  if (typeof count === 'number') {
    args.push('COUNT', count);
  }
  args.push('STREAMS', stream, id);
  const result = await redis.xread(...args);
  return normalizeStreamEntries(result);
}

/**
 * Reads entries from Redis stream using XREADGROUP (consumer group).
 */
export async function xreadgroupStream(redis, { stream, group, consumer, count = 10, block }) {
  const args = ['GROUP', group, consumer];
  if (typeof block === 'number') {
    args.push('BLOCK', Math.max(0, block));
  }
  if (typeof count === 'number') {
    args.push('COUNT', count);
  }
  args.push('STREAMS', stream, '>');
  const result = await redis.xreadgroup(...args);
  return normalizeStreamEntries(result);
}

/**
 * Polls Redis until key exists or attempts exhausted.
 * Used to verify replication lag after writes.
 */
export async function ensureKeyExists(redis, key, { attempts = 20, intervalMs = 25 } = {}) {
  if (!redis) {
    throw new Error('redis client is required');
  }
  if (attempts <= 0) {
    return true;
  }
  for (let i = 0; i < attempts; i += 1) {
    const exists = await redis.exists(key);
    if (exists) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return false;
}
