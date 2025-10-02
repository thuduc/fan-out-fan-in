# Product Requirements Document: XML Valuation Processing System

## 1. Overview

The XML Valuation Processing System is a distributed platform for processing financial instrument valuation requests submitted as XML documents. The system orchestrates complex, multi-step valuation workflows where tasks must be executed in ordered groups, with each group depending on results from previous groups.

## 2. Core Functional Requirements

### 2.1 XML Request Processing

**FR-2.1.1: XML Request Structure**
- The system SHALL accept XML requests containing one or more groups
- Each group SHALL contain one or more valuation tasks
- Each valuation task represents an evaluation of a financial instrument (e.g., home loan, security, schedule)
- XML requests SHALL be accepted up to 10MB in size

**FR-2.1.2: Group Ordering**
- Groups SHALL be processed sequentially in the order they appear in the request
- Processing of group N SHALL NOT begin until ALL tasks in group N-1 have completed successfully
- This ordering constraint exists because group N tasks require results from group N-1 tasks

**FR-2.1.3: Task Generation**
- The system SHALL parse the incoming XML request to identify groups and tasks
- For each group, the system SHALL generate individual task XML requests
- Task XML requests for group N SHALL be generated using:
  - The original XML request data
  - Results from all completed tasks in group N-1

**FR-2.1.4: Result Assembly**
- After all groups complete successfully, the system SHALL assemble a final XML response
- The response SHALL aggregate results from all task executions across all groups
- The system SHALL store the assembled response for retrieval

### 2.2 Request Submission API

**FR-2.2.1: Asynchronous Submission**
- The system SHALL provide a REST endpoint `POST /valuation?sync=N` that accepts XML payloads
- The request body SHALL contain the valuation XML
- Content-Type SHALL be `application/xml` or `text/xml`
- The system SHALL immediately return HTTP 202 Accepted with a unique request identifier (UUID)
- Processing SHALL continue asynchronously after the response is returned

**FR-2.2.2: Synchronous Submission**
- The system SHALL provide a REST endpoint `POST /valuation?sync=Y` that accepts XML payloads
- The system SHALL block and wait for request processing to complete
- Upon successful completion, the system SHALL return HTTP 200 with the final XML response
- Upon failure, the system SHALL return HTTP 500 with error details
- If processing exceeds a timeout threshold, the system SHALL return HTTP 202 with the request identifier to allow polling

**FR-2.2.3: Request Validation**
- The system SHALL validate that submitted XML is well-formed
- The system SHALL reject payloads exceeding the maximum size limit (10MB) with HTTP 413
- The system SHALL reject invalid `sync` parameter values with HTTP 400
- The system SHALL generate a unique UUID v4 identifier for each accepted request

### 2.3 Request Status Tracking

**FR-2.3.1: Status Endpoint**
- The system SHALL provide a REST endpoint `GET /valuation/:requestId/status`
- The system SHALL return the current processing status for the specified request
- If the request ID does not exist, the system SHALL return HTTP 404

**FR-2.3.2: Status Information**
- Status responses SHALL include:
  - Request identifier
  - Current lifecycle state (e.g., received, started, succeeded, failed, pending)
  - Current group being processed
  - Total number of groups
  - Submission timestamp
  - Completion timestamp (if applicable)

**FR-2.3.3: Lifecycle States**
- The system SHALL track requests through the following states:
  - `received`: Request accepted and persisted
  - `started`: Processing has begun
  - `group_started`: A specific group has begun processing
  - `group_completed`: A specific group has completed successfully
  - `succeeded`: All groups completed successfully
  - `failed`: Processing failed due to errors

### 2.4 Result Retrieval

**FR-2.4.1: Results Endpoint**
- The system SHALL provide a REST endpoint `GET /valuation/:requestId/results`
- The system SHALL return the final XML response for completed requests
- Content-Type SHALL be `application/xml`

**FR-2.4.2: Result Availability**
- If results are available, the system SHALL return HTTP 200 with the XML response
- If the request is still processing, the system SHALL return HTTP 404 with a message indicating results are not yet available
- If the request failed, the system SHALL return HTTP 422 with failure details
- If the request has expired/been purged, the system SHALL return HTTP 410 Gone

### 2.5 Task Execution

**FR-2.5.1: Task Processing**
- Each task SHALL execute independently within its group
- Tasks within the same group SHALL execute in parallel
- The system SHALL execute valuation logic for each task using the task XML
- Each task SHALL produce a result that is persisted for later aggregation

**FR-2.5.2: Task Completion Tracking**
- The system SHALL track the number of expected tasks per group
- The system SHALL track the number of completed tasks per group
- The system SHALL track the number of failed tasks per group
- The system SHALL only advance to the next group when all tasks in the current group have completed

### 2.6 Error Handling and Retry

**FR-2.6.1: Task Failure Handling**
- When a task fails, the system SHALL record the failure
- The system SHALL retry failed tasks up to a configurable maximum (e.g., 3 attempts)
- Each retry attempt SHALL be tracked with an attempt number

**FR-2.6.2: Terminal Failures**
- If a task exceeds maximum retry attempts, the system SHALL mark the entire group as failed
- If a group fails, the system SHALL mark the entire request as failed
- Failed requests SHALL NOT proceed to subsequent groups

**FR-2.6.3: Failure Reporting**
- The system SHALL store detailed failure information including:
  - Failed task identifiers
  - Error codes and messages
  - Number of retry attempts
  - Timestamp of final failure
- Failure information SHALL be available via the status and results endpoints

### 2.7 Data Persistence and Caching

**FR-2.7.1: Request Storage**
- The system SHALL store the original XML request upon submission
- The system SHALL store generated task XML requests for each task
- The system SHALL store task results from each execution
- The system SHALL store the final assembled response XML

**FR-2.7.2: State Management**
- The system SHALL persist request lifecycle state
- The system SHALL persist per-group execution state including:
  - Expected task count
  - Completed task count
  - Failed task count
  - Group status

**FR-2.7.3: Data Retention**
- The system SHALL apply a configurable time-to-live (TTL) to stored data (e.g., 24 hours)
- The system SHALL purge expired request data after the retention period

### 2.8 XML Reference Resolution (Hydration)

**FR-2.8.1: External File References**
- The system SHALL support `<element href="s3://bucket/key">` syntax to reference external S3 objects
- The system SHALL support `<element href="file://path">` syntax to reference local files
- When hydrating, the system SHALL fetch the referenced content and replace the element content

**FR-2.8.2: XPath Selection**
- The system SHALL support `<vn:select path="..." source="...">` syntax for extracting data via XPath expressions
- The system SHALL evaluate XPath expressions against specified source documents
- The system SHALL insert the selected nodes into the target location

**FR-2.8.3: Dynamic Function Invocation**
- The system SHALL support `<vn:use function="...">` syntax to invoke functions for dynamic value generation
- Function results SHALL be inserted at the target location

**FR-2.8.4: Attribute Preservation**
- When rehydrating elements with external content, the system SHALL preserve local attributes and child nodes that do not conflict with the fetched content

### 2.9 Scalability and Concurrency

**FR-2.9.1: Multiple Concurrent Requests**
- The system SHALL process multiple requests concurrently
- Each request SHALL be processed independently
- The system SHALL scale horizontally to handle increased request volume

**FR-2.9.2: Parallel Task Execution**
- Within a group, all tasks SHALL be eligible for concurrent execution
- The system SHALL scale task processing capacity based on workload

**FR-2.9.3: Resource Limits**
- The system SHALL enforce payload size limits (10MB)
- The system SHALL enforce synchronous request timeout limits (configurable, default 120 seconds)

### 2.10 Idempotency

**FR-2.10.1: Request Idempotency**
- The system SHALL support an optional `Idempotency-Key` header on POST requests
- When an idempotency key is provided, the system SHALL return the same request ID for duplicate submissions
- Duplicate submissions with the same idempotency key SHALL NOT trigger reprocessing

**FR-2.10.2: Task Result Idempotency**
- The system SHALL prevent overwriting successful task results with stale retry data
- Task results SHALL include attempt numbers to ensure proper ordering

### 2.11 Observability

**FR-2.11.1: Correlation Tracking**
- All operations SHALL be tagged with the request identifier
- Group-level operations SHALL include the group index
- Task-level operations SHALL include the task identifier

**FR-2.11.2: Metrics and Monitoring**
- The system SHALL expose metrics for:
  - Request submission rate
  - Request completion rate
  - Request failure rate
  - Task processing duration
  - Group completion latency
  - Synchronous request timeout rate

**FR-2.11.3: Health Endpoint**
- The system SHALL provide a `/healthz` endpoint returning service health status
- Health checks SHALL verify connectivity to dependencies (e.g., Redis)

## 3. Request/Response Examples

### 3.1 Asynchronous Submission

**Request:**
```
POST /valuation?sync=N
Content-Type: application/xml

<vnml>
  <project>
    <group name="Group1">...</group>
    <group name="Group2">...</group>
  </project>
</vnml>
```

**Response:**
```
HTTP 202 Accepted
Content-Type: application/json

{
  "requestId": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 3.2 Synchronous Submission (Success)

**Request:**
```
POST /valuation?sync=Y
Content-Type: application/xml

<vnml>...</vnml>
```

**Response:**
```
HTTP 200 OK
Content-Type: application/xml

<vnml-response>...</vnml-response>
```

### 3.3 Synchronous Submission (Timeout)

**Request:**
```
POST /valuation?sync=Y
Content-Type: application/xml

<vnml>...</vnml>
```

**Response:**
```
HTTP 202 Accepted
Content-Type: application/json

{
  "requestId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending"
}
```

### 3.4 Status Query

**Request:**
```
GET /valuation/550e8400-e29b-41d4-a716-446655440000/status
```

**Response:**
```
HTTP 200 OK
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

### 3.5 Results Retrieval (Completed)

**Request:**
```
GET /valuation/550e8400-e29b-41d4-a716-446655440000/results
```

**Response:**
```
HTTP 200 OK
Content-Type: application/xml

<vnml-response>...</vnml-response>
```

### 3.6 Results Retrieval (Not Ready)

**Request:**
```
GET /valuation/550e8400-e29b-41d4-a716-446655440000/results
```

**Response:**
```
HTTP 404 Not Found
Content-Type: application/json

{
  "error": "Results not yet available"
}
```

## 4. Non-Functional Requirements

### 4.1 Performance

- Synchronous requests SHALL timeout after a configurable duration (default: 120 seconds)
- The system SHALL support requests up to 10MB in size
- Task execution SHALL complete within reasonable time bounds appropriate for financial valuations

### 4.2 Reliability

- The system SHALL ensure no data loss for accepted requests
- The system SHALL guarantee sequential group execution
- Task retries SHALL be tracked to prevent infinite retry loops

### 4.3 Availability

- The system SHALL continue processing existing requests during partial failures
- Individual task failures SHALL NOT impact other tasks in the same group

### 4.4 Security

- The system SHALL validate XML structure to prevent malformed input
- The system SHALL enforce payload size limits to prevent resource exhaustion
- The system SHALL support secure access to external resources (S3, files)

### 4.5 Maintainability

- Request and task data SHALL be automatically purged after the retention period
- The system SHALL provide clear error messages for failures
- Correlation identifiers SHALL enable tracing across distributed components

## 5. Out of Scope

The following are explicitly NOT part of this product's functional requirements:

- Authentication and authorization mechanisms
- Business logic for financial valuation calculations (this is a black box within task processors)
- Specific XML schema validation beyond well-formedness
- Multi-tenancy and user management
- External notification mechanisms (webhooks, emails)
- Request prioritization or SLA-based scheduling
- Audit logging and compliance reporting
- Cost allocation and billing
- Disaster recovery and backup strategies
