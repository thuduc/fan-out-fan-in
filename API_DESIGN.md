# vnapi REST API Design

## 1. Goals & Scope
- Extend the existing Node.js vnapi with an Express-based REST API that handles inbound XML valuation requests and exposes status/result retrieval endpoints.
- Maintain compatibility with the current Redis-driven orchestration model documented in `DESIGN.md` by reusing the `stream:request:ingest` ingress flow, lifecycle stream, and cache/state keys.
- Provide synchronous (`sync=Y`) and asynchronous (`sync=N`) submission semantics without impacting existing stream consumers or Lambda orchestration.

## 2. Service Additions (vnapi)
- Introduce an Express HTTP server hosted alongside the existing polling loop. The service will start the REST API and the Redis stream consumer within the same process, sharing Redis connections/pools.
- Add a thin application layer (`RequestSubmissionService`) responsible for:
  - Validating and normalising incoming XML payloads.
  - Generating request IDs (UUID v4) and storing XML in Redis cache.
  - Emitting ingestion events to `stream:request:ingest` using the existing envelope format consumed by the main orchestrator.
  - Optionally awaiting completion via Redis streams for synchronous submissions.
- Provide a `RequestQueryService` to encapsulate state/result lookups (`state:request:<id>`, `cache:request:<id>:response`).
- HTTP server wiring:
  - `server.js` (new entry point) bootstraps Express, registers routes, and holds references to the request services and orchestrator polling loop.
  - Graceful shutdown hooks stop the orchestrator poller and close Redis connections when the HTTP server terminates.

## 3. Redis Usage Enhancements
- **Request Cache**: continue storing inbound XML at `cache:request:<id>:xml`. Use `SET` with TTL (e.g., 24h configurable) to align with existing retention.
- **Synchronous Lifecycle Waiting**:
  - Use `XREAD` (non-group) on `stream:request:lifecycle` starting at `>` to read only future lifecycle entries.
  - For each synchronous POST, maintain the last seen stream ID and block (`BLOCK <timeout>`) until an entry with matching `requestId` and terminal status (`completed`, `failed`) appears.
  - Because `XREAD` without consumer groups does not create pending entries, no explicit `XACK`/`XGROUP` management is required, avoiding interference with other lifecycle consumers.
  - Enforce a configurable timeout (default 120s) before returning a pending response to the caller.
- **Response Cache**: request orchestrator already writes results to `cache:request:<id>:response`; the API reads this key for sync responses and GET `/results`.

## 4. Endpoint Specifications
### 4.1 POST `/valuation?sync=Y|N`
- **Input**: XML payload in request body (Content-Type `application/xml` or `text/xml`). Optional headers for metadata (e.g., `X-Requester`, `X-Priority`).
- **Processing Steps**:
  1. Validate `sync` query parameter (default `N`). Reject unsupported values with `400`.
  2. Validate XML syntactically (optionally using a lightweight parse to ensure well-formedness; no business schema validation at this layer).
  3. Generate `requestId = uuid.v4()`.
  4. Persist XML to Redis cache `cache:request:<requestId>:xml` (`SET` with TTL) and optionally metadata hash.
  5. Add ingestion entry to `stream:request:ingest` with fields:
     - `requestId`, `xmlKey`, `metadataKey`, `responseKey`, `groupCount` (if derivable), `submittedAt`.
     - Additional metadata field serialised as JSON (`metadata`) if headers supplied.
  6. If `sync=N`:
     - Respond `202 Accepted` with `{ requestId }`.
  7. If `sync=Y`:
     - Invoke lifecycle wait routine: block on `XREAD` against `stream:request:lifecycle` until a terminal status for `requestId` is observed or the timeout elapses.
     - Upon receiving `completed`, read response XML from `cache:request:<id>:response` and return `200 OK` with XML body (Content-Type `application/xml`).
     - If `failed`, fetch failure detail from `cache:request:<id>:failure` (if present) and return `500` with JSON describing the failure.
     - On timeout, return `202 Accepted` with `{ requestId, status: 'pending' }` to allow client polling.
- **Concurrency**: Each synchronous request uses an independent wait loop; to avoid spinning, apply exponential backoff between `XREAD` calls when the stream returns unrelated entries.

### 4.2 GET `/valuation/:requestId/status`
- **Behaviour**:
  1. Retrieve `state:request:<requestId>` hash via `HGETALL`.
  2. If missing, respond `404`.
  3. Map stored status values (`received`, `started`, `succeeded`, `failed`, etc.) to a concise response JSON:
     ```json
     {
       "requestId": "...",
       "status": "succeeded",
       "currentGroup": 2,
       "groupCount": 3,
       "receivedAt": "...",
       "completedAt": "..."
     }
     ```
  4. Include derived booleans (e.g., `isTerminal`) or aggregated counts if available.
- **Headers**: Support conditional caching (`Cache-Control: no-store`) because status is dynamic.

### 4.3 GET `/valuation/:requestId/results`
- **Behaviour**:
  1. Attempt `GET cache:request:<requestId>:response`.
  2. If result exists, return `200` with XML payload (`Content-Type: application/xml`). Optionally include `ETag` derived from value hash to aid caching.
  3. If missing, check `state:request:<requestId>`.
     - If status is still processing, respond `404` with a clear message indicating the result is not yet available.
     - If status indicates failure, respond `422` with failure summary (optionally retrieving from `cache:request:<id>:failure`).
     - If the state key is absent (expired), respond `410 Gone`.

## 5. Interaction with Existing Components
- **vnapi Stream Consumer** remains unchanged; it processes entries emitted by the new API exactly like external producers.
- **vnvs / vnas** require no modifications; they continue reading and writing Redis data as defined in `DESIGN.md`.
- **Lifecycle Publisher** already writes to `stream:request:lifecycle`, enabling synchronous waits. Ensure lifecycle events include `requestId` field (existing behaviour) so API waiters can filter accurately.
- **State Repository** keys remain consistent; API read operations rely on existing storage logic executed by vnapi and vnvs.

## 6. Concurrency & Failure Considerations
- **Synchronous Wait Timeouts**: Configurable (e.g., via env `SYNC_WAIT_TIMEOUT_MS`). On timeout, request stays active in the system; clients can poll status/results endpoints.
- **Redis Stream Backlog**: Using `XREAD` avoids pending entries. The API discards unrelated lifecycle messages by keeping the last seen ID and reissuing `XREAD` with that ID to ensure no messages are skipped.
- **Idempotency**: If POST is retried (network issues), support optional `Idempotency-Key` header. Store a mapping (`request:idempotency:<key> -> requestId`) using `SETNX`; when present, return previously generated `requestId`.
- **Validation Errors**: Malformed XML returns `400`. For XML >1MB (per requirement), enforce payload limit (Express `raw-body` middleware) and return `413 Payload Too Large` if exceeded.

## 7. Configuration & Deployment
- New environment variables:
  - `HTTP_PORT` (default 8080).
  - `SYNC_WAIT_TIMEOUT_MS` (default 120000).
  - `PAYLOAD_MAX_BYTES` (default 1048576).
  - `REQUEST_TTL_SECONDS` (matches existing retention policy).
- Health endpoint `/healthz` (optional) returning orchestrator + Redis connectivity status.
- Update service Dockerfile/entrypoint to `node server.js` (which internally bootstraps both Express server and orchestrator polling). Ensure `MainOrchestrator.startPolling()` is awaited after server start.

## 8. Testing Strategy
- **Unit Tests**:
  - Controller-level tests using supertest to validate request validation, sync timeout behaviour (mock Redis client).
  - Service tests verifying Redis interactions (XML storage, stream emission, lifecycle waiting) with stubbed Redis.
- **Integration Tests**:
  - Spin up Redis (test container) and run end-to-end: POST sync, ensure orchestrator/test double publishes lifecycle events, verify response handling.
- **Regression**:
  - Re-run existing orchestrator unit tests to ensure no regressions.
  - Add contract tests for GET endpoints verifying behaviour across statuses (`received`, `started`, `succeeded`, `failed`).

## 9. Observability & Telemetry
- Log each submission with request ID, sync flag, ingestion stream message ID.
- Emit metrics (e.g., CloudWatch) for request counts, sync wait latency, timeout rate, error categories.
- Include correlation ID header propagation to downstream components via lifecycle metadata.

## 10. Backward Compatibility & Rollout
- Enabling the REST API does not alter existing stream consumption; upstream producers can continue pushing directly to `stream:request:ingest`.
- Rollout plan:
  1. Deploy new vnapi version with REST API disabled by feature flag (`ENABLE_HTTP=false`).
  2. Once stable, enable HTTP to accept traffic.
  3. Monitor Redis stream lag and API latency to ensure synchronous waits do not starve orchestrator polling (consider worker concurrency to isolate HTTP handling threads).

