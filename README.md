# fan-out-fan-in
A Fan-out/Fan-in implementation using Node.js and Redis

Used Codex CLI with gpt-5-codex high to generate the REQUIREMENTS.md using the following prompt:
- See REQUIREMENTS.md and follow the instructions as specified in this requirements specification for a new system design

After review of the REQUIREMENTS.md, we proceeded with the implementation using the following prompt:
- Go ahead and implement the design specified in DESIGN.md fully. Make sure each service (Main Orchestrator, Request Orchestrator, and Task Processor) is self-contained and resides under its own directory so each can be built and deployed independently of one another. For Node.js, use Node 20. For Python, use Python 3.11. Also create unit tests for each service.

Note:
- main-orchestrator is the API. We will need to api Rest API support.

Used Codex CLI with gpt-5-codex high to generate the API_REQUIREMENTS.md using the following prompt:
- See API_REQUIREMENTS.md and follow the instructions as specified in this requirements specification

After review of the API_REQUIREMENTS.md, we proceeded with the implementation using the following prompt:
- Go ahead and implement the design specified in API_REQUIREMENTS.md fully

## Running the Express API
The API lives under `services/main-orchestrator` and exposes the valuation endpoints documented in `API_DESIGN.md`. It expects a Redis instance (configure via `REDIS_URL`) for request state and lifecycle coordination.

1. Install Node.js 20.x and ensure it is the active runtime.
2. From the repository root run:
   ```bash
   cd services/main-orchestrator
   npm install   # optional if you want to replace the bundled shim with real packages
   npm start
   ```
   `npm start` launches `src/server.js`, which wires the Express routes and starts the orchestration poller.
3. Submit a valuation request (asynchronous by default):
   ```bash
   curl -X POST \
        -H 'Content-Type: application/xml' \
        --data-binary @./request.xml \
        'http://localhost:8080/valuation?sync=N'
   ```
   The response contains the generated `requestId` you can later query.
4. Check status or retrieve results:
   ```bash
   curl 'http://localhost:8080/valuation/<requestId>/status'
   curl 'http://localhost:8080/valuation/<requestId>/results'
   ```
5. For synchronous processing use `sync=Y`; the API blocks (up to the configured timeout) until the lifecycle reaches a terminal state:
   ```bash
   curl -X POST \
        -H 'Content-Type: application/xml' \
        --data-binary @./request.xml \
        'http://localhost:8080/valuation?sync=Y'
   ```

Environment variables affecting the API (all optional and documented in `src/config.js`):
- `HTTP_PORT` – listening port (default `8080`).
- `PAYLOAD_MAX_BYTES` – maximum XML payload size (default `1048576`).
- `SYNC_WAIT_TIMEOUT_MS` – synchronous wait timeout (default `120000`).
- `REQUEST_TTL_SECONDS` – Redis TTL for cached artifacts (default `86400`).

## Next Steps
1. Integrate a real Redis client and AWS Lambda invoker in server.js.
2. Add end-to-end tests that exercise the new HTTP layer against live Redis to validate sync/async behaviour.

## Running Tests

All suites require access to Redis. If you do not already have an instance locally, start a disposable container:

```
docker run --rm -p 6379:6379 redis:7
```

### Node.js (main-orchestrator)

From `services/main-orchestrator`:

- **Unit tests** (Express layer and orchestrator logic only):

  ```bash
  node --test test/api.test.js test/mainOrchestrator.test.js
  ```

- **Integration tests** (full fan-out/fan-in flow with the Python workers):

  ```bash
  node --test test/integration.test.js
  ```

- **Full suite** (default):

  ```bash
  npm test
  ```

### Python services

Both Lambda-style services ship with virtual environments. Activate the venv, install dependencies, run the tests, and deactivate when finished.

#### Request Orchestrator

```bash
cd services/request-orchestrator
source venv/bin/activate
pip install -r requirements.txt
python -m unittest
deactivate
```

#### Task Processor

```bash
cd services/task-processor
source venv/bin/activate
pip install -r requirements.txt
python -m unittest
deactivate
```

The Python suites connect to dedicated Redis databases (DB 13 and 14) so they can run in parallel with the Node.js tests without interfering with each other.
