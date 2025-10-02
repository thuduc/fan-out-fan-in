# System Design: XML Valuation Processing System

## 1. System Overview

The XML Valuation Processing System is a distributed, event-driven platform for orchestrating financial instrument valuation workflows. The system employs a fan-out/fan-in pattern to process XML requests containing ordered groups of valuation tasks, where each group must complete before the next begins due to data dependencies.

### 1.1 Design Principles

- **Event-Driven Architecture**: All inter-service communication uses Redis Streams for asynchronous, reliable message passing
- **Separation of Concerns**: Three distinct microservices handle API ingestion, request orchestration, and task execution
- **Stateless Workers**: Task processors are stateless; all state resides in Redis
- **Sequential Group Processing**: Group N cannot begin until group N-1 completes successfully
- **Parallel Task Execution**: Within a group, tasks execute concurrently
- **Large Payload Handling**: XML payloads (up to 10MB) stored in Redis Cache with stream messages containing only references

## 2. System Architecture

### 2.1 Service Components

#### 2.1.1 vnapi (API Gateway & Main Orchestrator)
**Technology**: Node.js 20+ running on AWS Fargate

**Responsibilities**:
- Expose REST API for request submission and status queries
- Validate incoming XML requests
- Generate unique request identifiers (UUID v4)
- Store XML payloads in Redis Cache
- Consume `stream:request:ingest` using consumer group pattern
- Asynchronously invoke vnvs Lambda for each request
- Track high-level request lifecycle
- Publish lifecycle events to `stream:request:lifecycle`

**Key Modules**:
- `httpApp.js`: Express REST API endpoint definitions
- `requestSubmissionService.js`: Request validation, ID generation, cache storage
- `requestQueryService.js`: Status and result retrieval logic
- `mainOrchestrator.js`: Redis Stream consumer for request ingestion
- `requestStateRepository.js`: Redis state management operations
- `lifecyclePublisher.js`: Lifecycle event publishing
- `lambdaInvoker.js`: AWS Lambda async invocation wrapper

#### 2.1.2 vnvs (Request Orchestrator)
**Technology**: Python 3.11 running on AWS Lambda

**Responsibilities**:
- Single-request lifecycle orchestrator
- Parse XML to identify groups and tasks
- Enforce sequential group execution constraint
- Generate task XML payloads for each group using:
  - Original request XML
  - Results from previous group (for group N > 0)
- Perform XML hydration (resolve external references)
- Dispatch tasks to `stream:task:dispatch`
- Monitor task completion via `stream:task:updates`
- Implement retry logic for failed tasks
- Assemble final response XML from task results
- Publish completion status to `stream:request:lifecycle`

**Key Modules**:
- `orchestrator.py`: Main orchestration logic and group sequencing
- `task_invoker.py`: Task dispatch wrapper
- `hydrator.py`: XML hydration coordinator
- `hydration/engine.py`: Hydration engine core
- `hydration/strategies.py`: Hydration strategies (href, select, use)
- `hydration/fetchers/`: Resource fetchers (S3, file, composite)

#### 2.1.3 vnas (Task Processor)
**Technology**: Python 3.11 running on AWS Lambda

**Responsibilities**:
- Stateless task execution worker
- Consume tasks from `stream:task:dispatch`
- Fetch task XML from Redis Cache
- Execute valuation logic (invoke external valuation script)
- Store task results in Redis Cache
- Publish completion/failure events to `stream:task:updates`

**Key Modules**:
- `processor.py`: Task execution logic
- `handler.py`: Lambda entry point

### 2.2 System Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                         Client Application                       │
└────────────┬────────────────────────────────────────────────────┘
             │ HTTP REST API
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    vnapi (Node.js / Fargate)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │  HTTP API    │  │ Stream       │  │ Lambda Invoker     │   │
│  │  Controller  │  │ Consumer     │  │                    │   │
│  └──────┬───────┘  └──────┬───────┘  └─────────┬──────────┘   │
│         │                  │                     │               │
└─────────┼──────────────────┼─────────────────────┼──────────────┘
          │                  │                     │
          │                  ▼                     │ invoke
          │         stream:request:ingest          │
          │                  │                     │
          │                  │                     ▼
          │                  │          ┌─────────────────────────┐
          ▼                  │          │  vnvs (Python/Lambda)   │
    Redis Cache              │          │  ┌──────────────────┐   │
    ┌────────────┐          │          │  │  Orchestrator    │   │
    │ XML Store  │          │          │  │  - Parse XML     │   │
    │ Results    │          │          │  │  - Hydrate       │   │
    │ State      │◄─────────┴──────────┼──│  - Gen Tasks     │   │
    └────────────┘                     │  │  - Monitor       │   │
          ▲                             │  │  - Assemble      │   │
          │                             │  └────────┬─────────┘   │
          │                             └───────────┼─────────────┘
          │                                         │
          │                                         │ dispatch
          │                                         ▼
          │                              stream:task:dispatch
          │                                         │
          │                                         │ consume
          │                                         ▼
          │                             ┌─────────────────────────┐
          │                             │  vnas (Python/Lambda)   │
          │                             │  ┌──────────────────┐   │
          └─────────────────────────────┼──│  Task Processor  │   │
                                        │  │  - Fetch XML     │   │
                                        │  │  - Execute       │   │
                                        │  │  - Store Result  │   │
                                        │  └────────┬─────────┘   │
                                        └───────────┼─────────────┘
                                                    │
                                                    ▼
                                         stream:task:updates
                                                    │
                                                    │ (consumed by vnvs)
                                                    ▼
```

## 3. Data Model

### 3.1 Redis Streams

#### 3.1.1 stream:request:ingest
**Purpose**: Ingress queue for new valuation requests

**Producer**: vnapi (HTTP API submission)

**Consumer**: vnapi (MainOrchestrator with consumer group `orchestrator-main`)

**Message Fields**:
```javascript
{
  requestId: "uuid-v4",
  xmlKey: "cache:request:<requestId>:xml",
  responseKey: "cache:request:<requestId>:response",
  metadataKey: "cache:request:<requestId>:metadata",
  groupCount: 2,
  submittedAt: "2025-10-02T10:30:00Z"
}
```

#### 3.1.2 stream:request:lifecycle
**Purpose**: Request state change notifications

**Producer**: vnapi, vnvs

**Consumers**: vnapi (for sync request waiting), monitoring systems

**Message Fields**:
```javascript
{
  requestId: "uuid-v4",
  status: "received" | "started" | "group_started" | "group_completed" | "succeeded" | "failed",
  groupIndex: 0,  // optional, for group-level events
  timestamp: "2025-10-02T10:30:00Z"
}
```

**Consumption Pattern**: Multi-subscriber (vnapi uses XREAD without consumer group for sync waits)

#### 3.1.3 stream:task:dispatch
**Purpose**: Task execution queue

**Producer**: vnvs

**Consumer**: vnas (consumer group `task-workers`)

**Message Fields**:
```javascript
{
  requestId: "uuid-v4",
  groupIndex: 0,
  groupName: "Group1",
  taskId: "task-uuid",
  payloadKey: "cache:task:<requestId>:<groupIdx>:<taskId>:xml",
  resultKey: "cache:task:<requestId>:<groupIdx>:<taskId>:result",
  attempt: 1,
  valuationName: "security"
}
```

#### 3.1.4 stream:task:updates
**Purpose**: Task completion/failure notifications

**Producer**: vnas

**Consumer**: vnvs (request-scoped consumer group `req::<requestId>`)

**Message Fields**:
```javascript
{
  requestId: "uuid-v4",
  groupIndex: 0,
  taskId: "task-uuid",
  status: "completed" | "failed",
  resultKey: "cache:task:<requestId>:<groupIdx>:<taskId>:result",
  error: "error message",  // only for failed status
  attempt: 1,
  durationMs: 1234
}
```

### 3.2 Redis Cache Keys

#### 3.2.1 Request Data
```
cache:request:<requestId>:xml              # String - Original XML request
cache:request:<requestId>:response         # String - Final XML response
cache:request:<requestId>:metadata         # Hash - Request metadata (headers, etc.)
cache:request:<requestId>:failure          # String - Failure details (JSON)
```

#### 3.2.2 Task Data
```
cache:task:<requestId>:<groupIdx>:<taskId>:xml     # String - Task XML payload
cache:task:<requestId>:<groupIdx>:<taskId>:result  # String - Task result XML
```

#### 3.2.3 State Keys
```
state:request:<requestId>                          # Hash - Request lifecycle state
state:request:<requestId>:group:<groupIdx>         # Hash - Group completion tracking
```

**state:request:<requestId> Fields**:
```javascript
{
  status: "received" | "started" | "succeeded" | "failed",
  xmlKey: "cache:request:<requestId>:xml",
  responseKey: "cache:request:<requestId>:response",
  metadataKey: "cache:request:<requestId>:metadata",
  groupCount: 2,
  currentGroup: 0,
  retryCount: 0,
  receivedAt: "2025-10-02T10:30:00Z",
  submittedAt: "2025-10-02T10:30:00Z",
  completedAt: "2025-10-02T10:35:00Z"  // optional
}
```

**state:request:<requestId>:group:<groupIdx> Fields**:
```javascript
{
  expected: 10,      // Total tasks in group
  completed: 8,      // Successfully completed tasks
  failed: 2,         // Failed tasks
  status: "running" | "completed" | "failed"
}
```

### 3.3 TTL Policy

All cache and state keys are subject to configurable TTL (default: 24 hours) applied after request completion or failure.

## 4. API Specification

### 4.1 REST Endpoints

#### 4.1.1 POST /valuation
**Purpose**: Submit XML valuation request

**Query Parameters**:
- `sync`: `Y` (synchronous) or `N` (asynchronous, default)

**Request Headers**:
- `Content-Type`: `application/xml` or `text/xml`
- `Idempotency-Key`: (optional) unique key for idempotent submissions

**Request Body**: XML payload (max 10MB)

**Response (async, sync=N)**:
```http
HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "requestId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "accepted"
}
```

**Response (sync success, sync=Y)**:
```http
HTTP/1.1 200 OK
Content-Type: application/xml

<vnml-response>...</vnml-response>
```

**Response (sync timeout, sync=Y)**:
```http
HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "requestId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending"
}
```

**Error Responses**:
- `400 Bad Request`: Invalid sync parameter or malformed XML
- `413 Payload Too Large`: Exceeds 10MB limit
- `500 Internal Server Error`: Processing failure (sync only)

#### 4.1.2 GET /valuation/:requestId/status
**Purpose**: Query request processing status

**Response**:
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "requestId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "started",
  "currentGroup": 1,
  "groupCount": 2,
  "receivedAt": "2025-10-02T10:30:00Z",
  "completedAt": null
}
```

**Error Responses**:
- `404 Not Found`: Request ID does not exist

#### 4.1.3 GET /valuation/:requestId/results
**Purpose**: Retrieve final XML response

**Response (available)**:
```http
HTTP/1.1 200 OK
Content-Type: application/xml

<vnml-response>...</vnml-response>
```

**Response (not ready)**:
```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{
  "message": "Result not yet available",
  "status": "started"
}
```

**Error Responses**:
- `404 Not Found`: Request still processing
- `410 Gone`: Request expired/purged
- `422 Unprocessable Entity`: Request failed

#### 4.1.4 GET /healthz
**Purpose**: Service health check

**Response**:
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "ok"
}
```

## 5. Process Flows

### 5.1 Asynchronous Request Flow

```
1. Client → POST /valuation?sync=N
2. vnapi validates XML, generates requestId
3. vnapi stores XML → cache:request:<requestId>:xml
4. vnapi publishes → stream:request:ingest
5. vnapi returns 202 Accepted with requestId
6. MainOrchestrator consumes from stream:request:ingest
7. MainOrchestrator initializes state → state:request:<requestId>
8. MainOrchestrator publishes "received" → stream:request:lifecycle
9. MainOrchestrator invokes vnvs Lambda (async)
10. vnvs processes groups sequentially (see Group Processing Flow)
11. Client polls GET /valuation/:requestId/status
12. Client retrieves GET /valuation/:requestId/results when complete
```

### 5.2 Synchronous Request Flow

```
1. Client → POST /valuation?sync=Y
2. vnapi validates XML, generates requestId
3. vnapi stores XML → cache:request:<requestId>:xml
4. vnapi publishes → stream:request:ingest
5. vnapi blocks, reads stream:request:lifecycle (XREAD, non-group)
6. MainOrchestrator consumes from stream:request:ingest
7. MainOrchestrator initializes state → state:request:<requestId>
8. MainOrchestrator publishes "received" → stream:request:lifecycle
9. MainOrchestrator invokes vnvs Lambda (async)
10. vnvs processes groups (see Group Processing Flow)
11. vnvs publishes "succeeded" → stream:request:lifecycle
12. vnapi detects "succeeded" event for requestId
13. vnapi fetches cache:request:<requestId>:response
14. vnapi returns 200 OK with XML response
    (or 202 Accepted if timeout exceeded)
```

### 5.3 Group Processing Flow (vnvs)

```
For each group G in sequential order:

1. Parse group definition from XML
2. If G > 0, fetch previous group results from cache
3. Hydrate group XML (resolve href, select, use references)
4. Generate task XML for each valuation in group
5. Store task XML → cache:task:<requestId>:<G>:<taskId>:xml
6. Initialize group state → state:request:<requestId>:group:<G>
   - expected: N (task count)
   - completed: 0
   - failed: 0
   - status: "running"
7. Create consumer group req::<requestId> on stream:task:updates
8. Dispatch all tasks → stream:task:dispatch
9. Publish "group_started" → stream:request:lifecycle
10. Block on stream:task:updates (XREADGROUP)
11. For each task completion message:
    - Increment completed or failed counter
    - XACK message
12. When completed == expected and failed == 0:
    - Mark group status = "completed"
    - Publish "group_completed" → stream:request:lifecycle
    - Proceed to next group
13. If failures exceed retry limit:
    - Mark group status = "failed"
    - Mark request status = "failed"
    - Publish "failed" → stream:request:lifecycle
    - Exit

After all groups complete:
14. Fetch all task results from cache
15. Assemble final response XML
16. Store response → cache:request:<requestId>:response
17. Update state:request:<requestId> status = "succeeded"
18. Publish "succeeded" → stream:request:lifecycle
```

### 5.4 Task Processing Flow (vnas)

```
1. Consumer reads from stream:task:dispatch (XREADGROUP)
2. Parse message fields (requestId, taskId, payloadKey, etc.)
3. Fetch task XML → cache:task:<requestId>:<groupIdx>:<taskId>:xml
4. Execute valuation logic (invoke external script)
5. Store result → cache:task:<requestId>:<groupIdx>:<taskId>:result
6. Publish completion → stream:task:updates
   - status: "completed"
   - resultKey, attempt, durationMs
7. XACK message on stream:task:dispatch

On failure:
3. Catch exception
4. Publish failure → stream:task:updates
   - status: "failed"
   - error, attempt
5. XACK message
6. vnvs retries (up to MAX_TASK_RETRIES)
```

## 6. XML Hydration System

### 6.1 Hydration Overview

vnvs includes a multi-pass hydration engine that resolves external references and dynamic content in task XML before execution. Hydration is necessary because:
- Task XML may reference external files (S3, filesystem)
- Later groups need data from earlier group results
- Some values are computed dynamically

### 6.2 Hydration Strategies

#### 6.2.1 HrefHydrationStrategy
**Purpose**: Resolve `href` attributes pointing to external resources

**Syntax**:
```xml
<market href="s3://bucket/key/market.xml"/>
<model href="file:///path/to/model.xml"/>
```

**Behavior**:
- Fetch content from S3 or filesystem
- Replace element content with fetched XML
- Preserve local attributes and child nodes

#### 6.2.2 SelectHydrationStrategy
**Purpose**: Extract data via XPath from source documents

**Syntax**:
```xml
<vn:select path="//market[@name='Market1']" source="cache:request:<id>:xml"/>
```

**Behavior**:
- Evaluate XPath against source document
- Insert selected nodes at target location

#### 6.2.3 AttributeSelectHydrationStrategy
**Purpose**: Extract data via XPath for element attributes

**Syntax**:
```xml
<market select="/vnml/project/market[1]"/>
```

**Behavior**:
- Evaluate XPath from `select` attribute
- Replace element with selected node
- Merge local attributes

#### 6.2.4 UseFunctionHydrationStrategy
**Purpose**: Invoke functions for dynamic value generation

**Syntax**:
```xml
<vn:use function="getCurrentDate"/>
```

**Behavior**:
- Call registered function
- Insert return value at target location

### 6.3 Hydration Engine

**Multi-Pass Processing**: Strategies execute in sequence, allowing chained hydration (e.g., fetch from S3, then apply XPath selection)

**Deep Copy**: All hydration operates on deep-copied elements to preserve original XML

**Context Preservation**: Child nodes and attributes not affected by hydration are preserved

## 7. State Management

### 7.1 Request Lifecycle States

```
received → started → (group_started → group_completed)* → succeeded
                                                           ↓
                                                         failed
```

**State Transitions**:
1. `received`: vnapi accepts and caches request
2. `started`: vnvs begins processing
3. `group_started`: vnvs begins processing group N
4. `group_completed`: vnvs completes group N successfully
5. `succeeded`: All groups completed, response assembled
6. `failed`: Task failure exceeded retry limit or orchestration error

### 7.2 Group States

```
running → completed
    ↓
  failed
```

**Progression Logic**:
- `running`: Tasks dispatched, awaiting completion
- `completed`: All tasks succeeded (completed == expected, failed == 0)
- `failed`: Task failures exceeded retry threshold

### 7.3 State Consistency

**Authority**:
- vnapi: Owns request-level state initialization and lifecycle publishing
- vnvs: Owns group-level state and task retry decisions
- vnas: Stateless, publishes task outcomes only

**Concurrency**:
- Single vnvs instance per request (no concurrent orchestrators for same request)
- Multiple vnas instances may process tasks from same group concurrently

## 8. Error Handling & Retry Strategy

### 8.1 Task Failure Handling

**Strategy**: Optimistic retry with exponential backoff

**Configuration**:
- `MAX_TASK_RETRIES`: 3 (default)
- Retry decisions made by vnvs

**Flow**:
1. vnas executes task, catches exception
2. vnas publishes `status: "failed"` to stream:task:updates
3. vnvs receives failure notification
4. If `attempt < MAX_TASK_RETRIES`:
   - vnvs re-enqueues task with incremented attempt
   - Publishes new message to stream:task:dispatch
5. If `attempt >= MAX_TASK_RETRIES`:
   - Mark group as failed
   - Mark request as failed
   - Publish "failed" lifecycle event

### 8.2 Idempotency

**Task Results**:
- Results include `attempt` number
- Later attempts do not overwrite earlier successful results

**Request Submissions**:
- Support `Idempotency-Key` header
- Duplicate keys return same requestId without reprocessing

### 8.3 Stream Reliability

**Consumer Groups**:
- Use XREADGROUP for at-least-once delivery
- XACK only after processing completes
- XPENDING detection for stuck consumers

**Message Expiry**:
- Streams use XTRIM with MAXLEN to prevent unbounded growth

### 8.4 Lambda Timeout Handling

**Issue**: vnvs Lambda may timeout for long-running requests (many groups/tasks)

**Mitigation**: vnvs can self-reinvoke before timeout, persisting checkpoint in Redis state

## 9. Scalability & Performance

### 9.1 Horizontal Scaling

**vnapi (Fargate)**:
- Scales via ECS service task count
- Consumer group `orchestrator-main` distributes stream reads
- Shared Redis connections pooled per instance

**vnvs (Lambda)**:
- One Lambda invocation per request
- Concurrency limit configurable via AWS account settings
- Scales automatically with request volume

**vnas (Lambda)**:
- Scales with task backlog on stream:task:dispatch
- Consumer group `task-workers` distributes tasks
- Configure reserved concurrency to prevent throttling

### 9.2 Throughput Optimization

**Large Payload Handling**:
- XML stored in Redis Cache (not in streams)
- Stream messages contain only keys (minimal size)
- Avoids stream bloat, enables random access

**Parallel Task Execution**:
- All tasks within group dispatched simultaneously
- vnas workers process concurrently
- Limited only by Lambda concurrency limits

### 9.3 Resource Limits

**Payload Size**: 10MB (configurable via `PAYLOAD_MAX_BYTES`)

**Synchronous Timeout**: 120 seconds (configurable via `SYNC_WAIT_TIMEOUT_MS`)

**Redis Connection Pooling**: vnapi uses ioredis with connection reuse

## 10. Security Considerations

### 10.1 Data Protection

**In-Transit**:
- Redis connections use TLS (configurable)
- HTTP API served over HTTPS (load balancer termination)

**At-Rest**:
- Redis encryption-at-rest via AWS Elasticache settings
- S3 bucket encryption for external resources

### 10.2 Access Control

**Lambda Invocation**:
- vnapi uses IAM role with least-privilege permissions
- Only allowed to invoke vnvs ARN

**Redis Access**:
- Credentials stored in AWS Secrets Manager
- Network isolation via VPC security groups

**S3 Hydration**:
- vnvs IAM role grants read-only access to specific buckets

### 10.3 Input Validation

**XML Well-Formedness**:
- vnapi validates XML syntax before acceptance
- vnvs validates XML structure during parsing

**Payload Size**:
- Express middleware enforces 10MB limit
- Returns HTTP 413 for oversized payloads

## 11. Observability

### 11.1 Structured Logging

**Correlation IDs**:
- All log entries include `requestId`
- Group-level logs include `groupIndex`
- Task-level logs include `taskId`

**Log Levels**:
- `INFO`: State transitions, group/task start/completion
- `WARN`: Retries, timeouts
- `ERROR`: Failures, exceptions

### 11.2 Metrics

**vnapi**:
- Request submission rate (total, sync, async)
- Sync request latency (p50, p95, p99)
- Sync timeout rate

**vnvs**:
- Group processing duration
- Task retry rate
- Request success/failure rate

**vnas**:
- Task execution duration
- Task failure rate

**Redis**:
- Stream lag (pending messages)
- Consumer group lag
- Memory usage

### 11.3 Tracing

**Distributed Tracing**:
- Propagate `X-Trace-Id` header through lifecycle events
- Publish trace spans to CloudWatch or X-Ray

## 12. Data Retention & Cleanup

### 12.1 TTL Policy

**Default TTL**: 24 hours (configurable via `REQUEST_TTL_SECONDS`)

**Applied To**:
- All `cache:request:*` keys
- All `cache:task:*` keys
- All `state:request:*` keys

**Trigger**: TTL set upon request completion or failure

### 12.2 Stream Trimming

**Strategy**: XTRIM with approximate MAXLEN

**Configuration**:
- `stream:request:ingest`: 10,000 messages
- `stream:request:lifecycle`: 50,000 messages
- `stream:task:dispatch`: 100,000 messages
- `stream:task:updates`: 100,000 messages

### 12.3 Consumer Group Cleanup

**Scheduled Job**: Lambda function runs daily to:
- Identify completed/failed requests older than retention period
- Delete consumer groups `req::<requestId>` from stream:task:updates
- Remove associated state keys

## 13. Configuration Management

### 13.1 vnapi Environment Variables

```bash
HTTP_PORT=8080                          # API server listening port
REDIS_URL=redis://127.0.0.1:6379       # Redis connection string
PAYLOAD_MAX_BYTES=10485760             # Max XML size (10MB)
SYNC_WAIT_TIMEOUT_MS=120000            # Sync request timeout (2 min)
REQUEST_TTL_SECONDS=86400              # Cache TTL (24 hours)
LIFECYCLE_BLOCK_MS=1000                # Lifecycle stream poll interval
REQUEST_ORCHESTRATOR_ARN=arn:...       # vnvs Lambda ARN
```

### 13.2 vnvs Environment Variables

```bash
REDIS_URL=redis://127.0.0.1:6379       # Redis connection string
TASK_PROCESSOR_ARN=arn:...             # vnas Lambda ARN
MAX_TASK_RETRIES=3                     # Task retry limit
TASK_WAIT_TIMEOUT_MS=10000             # Task completion wait timeout
DEFAULT_BLOCK_MS=5000                  # Stream read block time
```

### 13.3 vnas Environment Variables

```bash
REDIS_URL=redis://127.0.0.1:6379       # Redis connection string
VNAS_SCRIPT=/path/to/vnas.sh           # Valuation execution script
```

## 14. Design Decisions & Rationale

### 14.1 Why Redis Streams over SQS?

**Advantages**:
- Consumer groups provide built-in load balancing
- Low latency (< 1ms) for local Redis
- Unified data store (cache + messaging)
- Stream history enables replay and debugging

**Trade-offs**:
- Single point of failure (mitigated by Redis replication)
- Requires explicit stream trimming

### 14.2 Why Separate vnapi and vnvs?

**Separation Benefits**:
- vnapi scales independently of request orchestration load
- Long-running request orchestration isolated in Lambda (auto-scaling)
- Clear boundary between API concerns and orchestration logic

### 14.3 Why Lambda for vnvs and vnas?

**Lambda Benefits**:
- Auto-scaling based on request/task volume
- Pay-per-use (no idle costs)
- Built-in fault tolerance (automatic retries for invocation failures)

**Trade-offs**:
- 15-minute timeout (vnvs may need self-reinvocation for very long requests)
- Cold start latency (mitigated by provisioned concurrency)

### 14.4 Why Consumer Groups for Task Updates?

**Request-Scoped Groups** (`req::<requestId>`):
- Each vnvs instance only sees updates for its request
- Prevents cross-request message interference
- Enables accurate completion tracking

**Trade-off**: Consumer groups must be cleaned up after request completion

### 14.5 Why XREAD (Non-Group) for Sync Waits?

**Avoids Interference**:
- vnapi sync waits do not create pending entries
- No XACK required (simpler logic)
- Does not compete with lifecycle event subscribers

**Trade-off**: No automatic retry if vnapi crashes during sync wait (client must poll)
