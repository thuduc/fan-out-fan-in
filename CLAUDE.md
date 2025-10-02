# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a distributed fan-out/fan-in XML request processing system built using Node.js, Python, Redis Streams, and AWS services (Fargate, Lambda). The system orchestrates parallel task processing across ordered groups, where each group must complete before the next begins.

### Core Architecture

The system uses three microservices:

1. **vnapi** (Node.js on AWS Fargate)
   - Entry point for XML valuation requests
   - Exposes REST API on port 8080
   - Consumes `stream:request:ingest` Redis Stream
   - Asynchronously invokes vnvs Lambda per request
   - Tracks high-level lifecycle states

2. **vnvs** (Python Lambda)
   - Single-request scope orchestrator
   - Enforces sequential group execution
   - Fans out tasks within a group to vnas
   - Waits for group completion before advancing
   - Assembles final response XML from task results

3. **vnas** (Python Lambda)
   - Stateless worker executing individual valuation tasks
   - Consumes `stream:task:dispatch` Redis Stream
   - Publishes results to `stream:task:updates`

### Redis Topology

**Streams:**
- `stream:request:ingest` - Ingress for new requests (consumed by vnapi)
- `stream:request:lifecycle` - Request state changes (multi-subscriber)
- `stream:task:dispatch` - Task execution queue (consumed by vnas)
- `stream:task:updates` - Task completion/error events (consumed by vnvs)

**Cache Keys:**
- `cache:request:<requestId>:xml` - Original XML payload
- `cache:request:<requestId>:response` - Final response XML
- `cache:task:<requestId>:<groupIdx>:<taskId>:xml` - Individual task XML
- `cache:task:<requestId>:<groupIdx>:<taskId>:result` - Task result

**State Keys:**
- `state:request:<requestId>` - Lifecycle state, active group, counters
- `state:request:<requestId>:group:<groupIdx>` - Group completion tracking

### Group Ordering Constraint

Groups must execute sequentially (group N cannot start until group N-1 completes) because later groups depend on results from prior groups. vnvs is the sole authority for group advancement.

### XML Hydration System

vnvs includes a hydration engine that resolves references in task XML:

- `<element href="s3://bucket/key">` - Fetches content from S3
- `<element href="file://path">` - Fetches from local filesystem
- `<vn:select path="..." source="...">` - XPath-based data extraction
- `<vn:use function="...">` - Function invocation for dynamic values

Hydration preserves local attributes and child nodes when rehydrating elements.

## Development Commands

### Running Tests

All tests require Redis running on localhost:6379. Start Redis with:
```bash
docker run --rm -p 6379:6379 redis:7
```

**vnapi (Node.js):**
```bash
cd services/vnapi

# Unit tests only (API and orchestrator logic)
node --test test/api.test.js test/mainOrchestrator.test.js

# Integration tests (full flow with Python workers)
node --test test/integration.test.js

# All tests
npm test
```

**vnvs (Python):**
```bash
cd services/vnvs
source venv/bin/activate
pip install -r requirements.txt

# Run all tests
python -m unittest discover -s tests -p "test_*.py" -v

# Run specific test file
python -m unittest tests.test_orchestrator -v
python -m unittest tests.test_select_hydration -v

deactivate
```

**vnas (Python):**
```bash
cd services/vnas
source venv/bin/activate
pip install -r requirements.txt

# Run all tests
python -m unittest discover -s tests -p "test_*.py" -v

# Run specific test file
python -m unittest tests.test_processor -v

deactivate
```

Python tests use dedicated Redis databases (DB 13 and 14) to avoid conflicts.

**Important Notes:**
- The virtual environment (venv) must exist in each Python service directory
- If venv doesn't exist, create it with: `python3 -m venv venv`
- All Python tests run against Redis on localhost:6379 (different DB numbers for isolation)
- **Python 3.12+ is required** for all Python services
- **Node.js 20+ is required** for the vnapi service

### Running the API

```bash
cd services/vnapi
npm install  # optional if dependencies already bundled
npm start    # launches src/server.js
```

**Environment Variables:**
- `HTTP_PORT` - Listening port (default: 8080)
- `REDIS_URL` - Redis connection URL (default: redis://127.0.0.1:6379)
- `PAYLOAD_MAX_BYTES` - Max XML payload size (default: 1048576)
- `SYNC_WAIT_TIMEOUT_MS` - Synchronous request timeout (default: 120000)
- `REQUEST_TTL_SECONDS` - Redis TTL for artifacts (default: 86400)

**API Endpoints:**

Submit request (async):
```bash
curl -X POST \
     -H 'Content-Type: application/xml' \
     --data-binary @./request.xml \
     'http://localhost:8080/valuation?sync=N'
```

Submit request (sync, blocks until complete):
```bash
curl -X POST \
     -H 'Content-Type: application/xml' \
     --data-binary @./request.xml \
     'http://localhost:8080/valuation?sync=Y'
```

Check status:
```bash
curl 'http://localhost:8080/valuation/<requestId>/status'
```

Retrieve results:
```bash
curl 'http://localhost:8080/valuation/<requestId>/results'
```

## Code Structure

**vnapi** (`services/vnapi/src/`):
- `server.js` - Entry point, bootstraps Express and orchestrator
- `httpApp.js` - Express route definitions
- `mainOrchestrator.js` - Stream consumer for request ingestion
- `requestSubmissionService.js` - XML validation, ID generation, Redis caching
- `requestQueryService.js` - Status/result lookups
- `requestStateRepository.js` - Redis state management
- `lifecyclePublisher.js` - Publishes lifecycle events
- `lambdaInvoker.js` - AWS Lambda invocation wrapper

**vnvs** (`services/vnvs/app/`):
- `orchestrator.py` - Main orchestration logic, group sequencing
- `task_invoker.py` - Task dispatch to Redis Stream
- `hydrator.py` - XML hydration coordinator
- `hydration/engine.py` - Core hydration engine
- `hydration/strategies.py` - Hydration strategies (href, select, use)
- `hydration/fetchers/` - Resource fetchers (S3, file, composite)

**vnas** (`services/vnas/app/`):
- `handler.py` - Lambda entry point
- `processor.py` - Task execution logic

## Important Implementation Details

- **Node.js version**: 20+ required
- **Python version**: 3.12+ required for all services (vnvs, vnas)
- **Synchronous requests**: vnapi uses `XREAD` (non-group) on lifecycle stream to avoid interfering with other consumers
- **Idempotency**: Task results include attempt number to prevent overwriting success with stale retry data
- **Error handling**: Task workers publish failures to `stream:task:updates`; vnvs retries up to `MAX_TASK_RETRIES` (default: 3)
- **Lambda timeout**: vnvs may self-reinvoke for long-running requests to avoid Lambda timeout
- **XML encoding**: Task XMLs use proper encoding handling for special characters (see services/vnapi/src/utils.js)

## Key Files for Understanding System

- `DESIGN.md` - Original distributed architecture design
- `API_DESIGN.md` - REST API design specification
- `REQUIREMENTS.md` - Original requirements
- `request.xml`, `model.xml`, `market.xml` - Sample XML files for testing