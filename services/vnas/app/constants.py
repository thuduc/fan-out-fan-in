"""Redis stream constants for vnas task processor."""

# Redis stream for task completion/failure events (consumed by vnvs orchestrator)
TASK_UPDATES_STREAM = "stream:task:updates"
