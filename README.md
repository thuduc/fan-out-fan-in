# fan-out-fan-in
A Fan-out/Fan-in implementation using Node.js and Redis

Used Codex CLI with gpt-5-codex high to generate the REQUIREMENTS.md using the following prompt:
- See REQUIREMENTS.md and follow the instructions as specified in this requirements specification for a new system design

After review of the REQUIREMENTS.md, we proceeded with the implementation using the following prompt:
- Go ahead and implement the design specified in DESIGN.md fully. Make sure each service (Main Orchestrator, Request Orchestrator, and Task Processor) is self-contained and resides under its own directory so each can be built and deployed independently of one another. For Node.js, use Node 20. For Python, use Python 3.11. Also create unit tests for each service.

Note:
- main-orchestrator is the API. We will need to api Rest API support.
