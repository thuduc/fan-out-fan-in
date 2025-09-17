# Distributed XML Request Processing Design

## 1. Objectives
- Orchestrate fan-out/fan-in processing of XML-defined valuation tasks while enforcing strict group ordering.
- Use Redis Streams for cross-service messaging/state progression and Redis Cache for storing large XML payloads/results.
- Separate orchestration responsibilities across AWS Fargate (Node.js main orchestrator) and AWS Lambda (Python request/task workers).
- Ensure horizontal scalability, idempotent execution, and observability across asynchronous microservices.

## 2. Actors & Responsibilities
### 2.1 Main Orchestrator (Node.js on AWS Fargate)
- Maintains a long-lived consumer on the global `stream:request:ingest` Redis Stream.
- Persists incoming request envelopes (metadata only) and adds request XML to Redis Cache.
- Asynchronously invokes the Request Orchestrator Lambda per request (`Invoke` with event payload referencing cache keys).
- Tracks high-level lifecycle states in Redis and emits status events to `stream:request:lifecycle` for downstream consumers (audit/UI).

### 2.2 Request Orchestrator (Python Lambda)
- Single-request scope; orchestrates sequential group execution.
- Rehydrates request context/task definitions from Redis Cache.
- Generates per-group task XMLs, stores them in Redis Cache, registers expectations, and pushes dispatch events onto `stream:task:dispatch`.
- Monitors completion events on `stream:task:updates` using a request-scoped consumer group; waits for all tasks in the current group to finish before advancing.
- Builds final request response XML from cached task results; stores it in Redis Cache and signals completion via `stream:request:lifecycle` and status keys.

### 2.3 Task Processor (Python Lambda)
- Triggered (via EventBridge rule or AWS Lambda poller) by entries on `stream:task:dispatch`.
- Fetches task XML/input from Redis Cache, executes valuation logic, persists results to Redis Cache, and publishes completion data to `stream:task:updates`.
- Emits errors/failures to the same updates stream with status metadata for retry decisions.

## 3. Redis Topology
### 3.1 Streams (all capped via `XTRIM` policies)
- `stream:request:ingest`: ingress events from upstream producers; consumed by Main Orchestrator using consumer group `orchestrator-main`.
- `stream:request:lifecycle`: state changes for requests (`received`, `started`, `group_completed`, `succeeded`, `failed`, etc.); multiple subscribers.
- `stream:task:dispatch`: queue of individual task execution requests; consumed by Lambda poller consumer group `task-workers`.
- `stream:task:updates`: completion/error events from task processors; read by request-scoped consumer groups (`req::<requestId>`), and optionally by observability tooling.

### 3.2 Redis Cache / Keyspace (Strings unless noted)
- `cache:request:<requestId>:xml`: original inbound XML.
- `cache:request:<requestId>:metadata` (Hash): summary (submission time, requester, group count, etc.).
- `cache:request:<requestId>:group:<groupIdx>:definition`: serialized subgroup definition and dependency metadata.
- `cache:task:<requestId>:<groupIdx>:<taskId>:xml`: generated task XML payload.
- `cache:task:<requestId>:<groupIdx>:<taskId>:result`: serialized task result (XML/JSON).
- `state:request:<requestId>` (Hash): lifecycle state, active group index, aggregate counts, retry counters.
- `state:request:<requestId>:group:<groupIdx>` (Hash): `expected`, `completed`, `failed`, `status` for gating progression.
- TTLs applied post-completion based on retention policy (e.g., 24h).

## 4. End-to-End Flow
### 4.1 Request Ingestion
1. Upstream producer uploads XML (HTTP/SQS/etc.).
2. Producer writes to Redis Cache `cache:request:<id>:xml` and adds metadata entry to `stream:request:ingest` (`requestId`, `payloadKey`, `groupCount`, etc.).
3. Main Orchestrator (Fargate) consumes the event, validates metadata, and records `state:request:<id>` with `status=received`.
4. Main Orchestrator emits `received` status on `stream:request:lifecycle` and asynchronously invokes Request Orchestrator Lambda with `{requestId}`.

### 4.2 Request Orchestration Per Group
1. Lambda loads metadata and group definitions from cache.
2. Creates a consumer group on `stream:task:updates` named `req::<requestId>` starting at `$` to receive only future events.
3. For group `g`:
   - Generate task XML payloads by combining base request data and prior group results (fetched from cache where needed).
   - Store each task XML under `cache:task:<requestId>:<g>:<taskId>:xml`.
   - Set `state:request:<id>:group:<g>` hash with `expected=<taskCount>`, `completed=0`, `failed=0`, `status=running`.
   - XADD entries to `stream:task:dispatch` with fields: `requestId`, `groupIdx`, `taskId`, `payloadKey`, `resultKey`, `attempt=1`.
   - Update lifecycle stream (`group_started`).
4. Await completion:
   - Use `XREADGROUP GROUP req::<requestId> orchestrator-consumer BLOCK <timeout>` on `stream:task:updates`.
   - For each message with matching `requestId` & `groupIdx`, increment `completed` or `failed`, record metrics, and `XACK`.
   - When `completed == expected` and `failed == 0`, mark group `completed` and proceed; if failures remain, decide retry or terminal failure (see Section 6).
5. After final group completes, compose response:
   - Gather task results from cache, build final XML, store in `cache:request:<id>:response`.
   - Update `state:request:<id>` (`status=succeeded`, `completedAt` timestamp) and emit `completed` event.
6. Notify upstream via callback mechanism (poller, webhook, or response stream entry referencing response cache key).

### 4.3 Task Processing
1. Lambda poller (custom or Redis-backed worker) reads from `stream:task:dispatch` via consumer group `task-workers`.
2. Worker fetches payload (`GET cache:task:...:xml`), executes business logic, and stores result to `cache:task:...:result`.
3. Worker XADDs to `stream:task:updates` with fields: `requestId`, `groupIdx`, `taskId`, `resultKey`, `status=completed`, `durationMs`, `workerId`.
4. On error, worker records failure result (`payloadKey` reused), increments `attempt`, and publishes `status=failed` with `errorCode`. Retry policy handled via Section 6.

## 5. Maintaining Group Ordering
- Request Orchestrator remains the single authority for group advancement; it does not dispatch group `g+1` until `state:request:<id>:group:<g>` indicates all tasks succeeded.
- Task processors are stateless and unaware of group ordering, preventing accidental parallelism across groups.
- The consumer group `req::<requestId>` ensures the orchestrator can block for completion notifications without missing events, even across Lambda re-invocations.
- For long-running requests, the orchestrator persists checkpoint (`state:request:<id>:currentGroup`, outstanding task IDs) and re-schedules itself via Lambda re-invocation (self-trigger or Step Functions) before hitting timeout.

## 6. Failure Handling & Retries
- Task workers do **not** retry automatically; they publish `status=failed`. Request Orchestrator decides to retry up to configured limit (e.g., 3).
- Retry flow: orchestrator re-enqueues failed task by XADDing new entry with incremented `attempt`, updates counters (`pendingRetries`).
- Poison pill handling: after max attempts, group marked `failed`; orchestrator emits `failed` lifecycle event and stores failure report in Redis (`cache:request:<id>:failure`).
- Stream reliability: use `XACK` only after processing; rely on `XPENDING` to detect stuck consumers. Operational agents can `XCLAIM` and reassign messages.
- Lambda idempotency: each task result write includes `eTag` or `attempt` to prevent overwriting success with stale retry data.

## 7. Scalability & Throughput
- Main Orchestrator horizontally scales via ECS service count; all instances share consumer group `orchestrator-main` to distribute request ingestion load.
- Request Orchestrator Lambdas scale per request; concurrency limits tuned via account settings.
- Task Lambdas scale with stream backlog; ensure sufficient concurrency settings and use batching (e.g., poll 10 entries).
- Redis cluster should use replication/sharding (Redis Enterprise or Elasticache) with stream key hashing to balance slots (`hash tags` like `{request:<id>}`).
- XML payload caching avoids inflating streams and allows random access by IDs.

## 8. Observability & Operations
- Metrics: track stream lag, group completion latency, Lambda duration, retry counts via CloudWatch & Redis INFO.
- Logging: include `requestId`, `groupIdx`, `taskId` correlation IDs; push structured logs to CloudWatch Logs.
- Alerts on: pending tasks exceeding SLA, repeated failures, Redis memory pressure, consumer group lag spikes.
- TTL cleanup job (scheduled Lambda) scans `state:request:*` for completed/failed requests beyond retention and purges cache entries and stream consumers/groups.
- Security: store Redis credentials in AWS Secrets Manager; enforce TLS in-transit. Use IAM roles for Lambda invocations and restrict network access via VPC security groups.

## 9. Extensibility Considerations
- Support alternate task processors by adding new consumer groups on `stream:task:dispatch` with filtering by `taskType` field.
- Incorporate prioritization by adding `priority` field to dispatch events and using separate consumer groups per priority bucket.
- Integrate external notification (SNS/email) by subscribing to `stream:request:lifecycle`.
- Allow partial responses by having orchestrator persist intermediate aggregates per group for downstream analytics before final completion.

## 10. Testing & Validation Strategy
- Unit-test orchestrator logic with Redis-mock to validate state transitions and stream message formatting.
- Integration tests using containerized Redis to simulate full request lifecycle, verifying ordering guarantees.
- Load testing: simulate high request volume, monitor Redis stream performance, Lambda concurrency, and cache hit latency.
- Chaos testing: inject task failures/timeouts, confirm retry and fallback behaviours.

