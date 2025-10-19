REQUEST_LIFECYCLE_STREAM = "stream:request:lifecycle"
REQUEST_STATE_KEY_TEMPLATE = "state:request:{request_id}"
GROUP_STATE_KEY_TEMPLATE = "state:request:{request_id}:group:{group_index}"
TASK_XML_KEY_TEMPLATE = "cache:task:{request_id}:{group_index}:{task_id}:xml"
TASK_RESULT_KEY_TEMPLATE = "cache:task:{request_id}:{group_index}:{task_id}:result"
DEFAULT_BLOCK_MS = 5000
TASK_WAIT_TIMEOUT_MS = 10000
MAX_TASK_RETRIES = 3


def task_updates_stream(request_id: str) -> str:
    """Returns the task updates stream name for a specific request."""
    return f"stream:task:updates:{request_id}"
