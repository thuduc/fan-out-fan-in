import crypto from 'crypto';

export function nowIso() {
  return new Date().toISOString();
}

export function ensureString(value, field) {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`Missing or invalid field: ${field}`);
  }
  return value;
}

export function ensureNumber(value, field) {
  const asNumber = Number(value);
  if (!Number.isFinite(asNumber)) {
    throw new Error(`Missing or invalid numeric field: ${field}`);
  }
  return asNumber;
}

export function generateExecutionToken() {
  return crypto.randomUUID();
}

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
