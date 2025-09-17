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
