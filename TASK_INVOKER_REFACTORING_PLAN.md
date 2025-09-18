# Task Invocation Refactor Plan

## Goal
Let the request orchestrator directly invoke the task-processor Lambda for each task, eliminating `stream:task:dispatch` as the primary dispatch mechanism while preserving group ordering, retries, and lifecycle visibility.

## Architectural Overview
1. **Request Submission Service**
   - Continue writing request XML and metadata to Redis.
   - Transactionally enqueue the request event and ensure payload visibility (either via Lua script or MULTI/EXEC) to avoid missing data races.

2. **Request Orchestrator**
   - Inject a new `TaskInvoker` alongside Redis and the logger.
   - `_dispatch_group` should still persist task payload/result keys, but instead of `XADD` it should invoke the task Lambda asynchronously per task.
   - Maintain the consumer group on `stream:task:updates` for completion/failure events.
   - Update `state:request:<id>` by calling `RequestStateRepository.setActiveGroup` and `markLifecycle` at each lifecycle step.
   - On invoke failure, retry with exponential backoff and move the task to a dead-letter bucket after `MAX_TASK_RETRIES`; publish `failed` lifecycle events and persist failure info (`cache:request:<id>:failure`).
   - Handle `_await_group_completion` timeouts by marking the request failed, updating lifecycle state, and optionally requeueing work.

3. **Task Processor Lambda**
   - Expose a direct handler that decodes the invocation payload, reads the task XML, executes the valuation, and publishes the completion/failure event to `stream:task:updates`.
   - Persist failure details in Redis so the API can surface diagnostics.

4. **Task Dispatcher Stream**
   - Deprecate `stream:task:dispatch` once direct invocation is in place. Optionally keep it for backward compatibility until all workers are migrated.

## Required Code Changes
- Create `TaskInvoker` (e.g., `services/request-orchestrator/app/task_invoker.py` or a JS equivalent if the orchestrator stays in Python) mirroring the existing `LambdaInvoker`.
- Modify `RequestOrchestrator.__init__` to accept the invoker and update `_dispatch_group` + `_await_group_completion` per above.
- Update the task-processor Lambda entry point to accept invocation payloads from the orchestrator.
- Replace Redis-based task worker shims in the Node integration tests with mocks/stubs for `TaskInvoker` so tests remain deterministic.

## Testing Strategy
1. **Unit**
   - Mock `TaskInvoker` in request orchestrator tests to assert invocation counts, retry/backoff behavior, and lifecycle updates.
   - Test the new task Lambda handler in isolation, verifying it writes results/failures and publishes updates.

2. **Integration**
   - Extend the existing Node integration harness to inject a fake `TaskInvoker` that synchronously executes a task processor stub, preserving end-to-end coverage without genuine AWS calls.
   - Add failure scenarios: task invoke failure, task execution failure, orchestrator timeout.

3. **Load Testing**
   - Perform targeted load tests (e.g., 1k requests → 100k tasks) to observe Lambda concurrency, request orchestrator CPU, and Redis load under the new model.

## Operational / Config Updates
- Introduce configuration for the task Lambda ARN, per-task invoke timeout, retry counts, and optional dead-letter stream.
- Update deployment scripts/infra (IaC) to supply the task Lambda ARN and grant the request orchestrator permissions to invoke it.
- Ensure CloudWatch metrics/logging capture: invoke success/failure counts, retry volume, task latency, request lifecycle state transitions.
- Document the new flow in README/API docs and note the removal/deprecation of `stream:task:dispatch`.

## Sequencing
1. Implement and test `TaskInvoker` + orchestrator changes behind a feature flag.
2. Update task processor Lambda handler.
3. Adjust integration tests to exercise the new path.
4. Roll out in stages, monitoring Lambda concurrency and failure rates.
5. Once stable, remove the old stream-based task dispatching and associated worker infrastructure.

