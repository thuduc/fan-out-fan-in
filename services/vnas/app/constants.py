"""Redis stream constants for vnas task processor."""


def task_updates_stream(request_id: str) -> str:
    """Returns the task updates stream name for a specific request."""
    return f"stream:task:updates:{request_id}"
