# Identified Gaps vs. DESIGN.md and API_DESIGN.md

## API Layer (`services/main-orchestrator`)
- **Missing Idempotency-Key handling**: API_DESIGN.md §6 calls for optional `Idempotency-Key` support backed by `request:idempotency:<key>` mappings. `requestSubmissionService.js` ignores this header; repeated submissions will produce duplicate work instead of reusing the original `requestId`.
- **Incomplete XML validation**: API_DESIGN.md §4.1 Step 2 suggests syntactic validation. `httpApp.js` only checks that the payload starts/ends with angle brackets; malformed XML will pass and be rejected later by the orchestrator. Consider attempting a lightweight parse (e.g., `xml2js` in a try/catch) before accepting the request.
- **Lifecycle-based responses lack failure detail**: `GET /valuation/:id/results` returns a `failure` payload if `cache:request:<id>:failure` exists, but neither the main nor request orchestrator currently create that key. Clients always get `detail: null`, defeating API_DESIGN.md §4.3 Step 3 requirement.
- **Expired requests return 404 instead of 410**: API_DESIGN.md §4.3 specifies a `410 Gone` when state data has expired. `httpApp.js` responds with `404` even if the absence is due to TTL expiry.
- **No `Cache-Control` headers on status/result responses**: The design recommends `Cache-Control: no-store` for dynamic endpoints. The Express handlers omit cache directives.

## Submission Pipeline
- **Request payload availability race**: Despite `ensureKeyExists` in `requestSubmissionService.js`, the request orchestrator still reaches `run()` without finding `cache:request:<id>:xml`, as seen in integration runs. The current behavior raises and terminates the Lambda without lifecycle updates. The main pipeline needs a first-class fix (e.g., retry/backoff or transactional write + XADD) so missing payloads are retried/logged instead of crashing.
- **No handling for `Idempotency-Key` collisions**: Beyond the missing storage, the submission service also doesn’t reject reuse of a key with a different payload, something the design implies should be handled.

## Request Orchestrator (`services/request-orchestrator/app/orchestrator.py`)
- **Failure flows incomplete**: DESIGN.md §6 expects the orchestrator to persist failure details (e.g., `cache:request:<id>:failure`), emit a `failed` lifecycle event, and update request state to `failed` when retries are exhausted. `_await_group_completion` raises `RuntimeError` but the caller never records failure state or publishes `failed`.
- **Timeout handling missing**: When `_await_group_completion` hits `TimeoutError`, `run()` propagates the exception without updating Redis state or scheduling a retry, contrary to DESIGN.md §6 (“re-schedules itself before hitting timeout”).
- **Request state not updated per group**: DESIGN.md §4.2 notes that the orchestrator should track the active group. `RequestStateRepository.setActiveGroup` is never called; `state:request:<id>` keeps `currentGroup=-1` for the entire lifecycle.
- **Metadata key ignored**: API_DESIGN.md describes storing request metadata alongside the XML. `run()` reads `metadataKey` from the event but never hydrates it, so any supplemental headers are unavailable during orchestration.
- **Failure detail collection missing**: Even when a task sends `status=failed`, the orchestrator never persists a human-readable summary for the API to surface.

## Task Processor (`services/task-processor/app/processor.py`)
- **No failure artifact written**: On exceptions, only a stream event is emitted. DESIGN.md §6 expects a failure report persisted (e.g., `cache:request:<id>:failure`) so the API can return diagnostics.
- **Lack of retry context**: Failed tasks are re-published with incremented `attempt`, but the processor does not include inspection data such as error codes or stack traces beyond `str(exc)`, limiting observability.

## Main Orchestrator (`services/main-orchestrator/src/mainOrchestrator.js`)
- **Request state never progresses beyond `received`**: DESIGN.md §4 states the orchestrator records lifecycle in `state:request:<id>` as it progresses. `_processEntry` initializes the state but never calls `RequestStateRepository.markLifecycle` or `setActiveGroup`, so state hashes do not reflect “started/succeeded/failed”.
- **Error handling leaves messages pending**: When `_processEntry` throws (e.g., Lambda invocation failure), the loop logs the error but does not requeue/ack. DESIGN.md §6 suggests the main orchestrator should retry or dead-letter; today’s behavior leaves the message pending without visibility.

## Configuration/Operational Concerns
- **`ENABLE_HTTP` feature flag absent**: API_DESIGN.md §10 proposes gating the HTTP listener behind `ENABLE_HTTP`. `server.js` always starts the Express app and poller.
- **README out-of-date on Redis usage**: The documentation still references an in-memory shim even though `server.js` now connects to real Redis. Consider updating or the doc will mislead deployers.

## Testing Coverage Observations
- **Python “unit” tests are integration-style**: `test_orchestrator.py` and `test_processor.py` hit Redis directly. That matches DESIGN.md’s emphasis on integration coverage but leaves fine-grained unit behavior (e.g., XML parsing helpers) untested.

## Suggested Fixes (High-level)
- Implement Idempotency-Key storage (`SETNX`) and reuse logic in `RequestSubmissionService`, aligning with API_DESIGN.md §6.
- Persist failure details (`cache:request:<id>:failure`), update request status to `failed`, and publish `failed` lifecycle events when retries exhaust or timeouts occur in the request orchestrator.
- Use `RequestStateRepository.setActiveGroup`/`markLifecycle` to keep `state:request:<id>` aligned with actual progress.
- Transactionally store the XML and publish the ingestion event (e.g., Lua script or `MULTI/EXEC`) so the orchestrator never sees a missing payload; add retry/backoff when it still happens.
- Enhance `httpApp.js` XML validation by attempting to parse or leveraging a proper XML parser.
- Return `410 Gone` for expired requests/results once state TTLs are implemented, matching API_DESIGN expectations.
- Add failure persistence to the task processor so API consumers receive meaningful diagnostics.
- Honor `ENABLE_HTTP` (or document why it’s omitted) and refresh the README to reflect the real Redis dependency.
*** End Patch
PATCH
