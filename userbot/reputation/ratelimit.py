"""Per-chat rate limit for the .check command.

In-memory for a single worker process; each worker only ever serves one
client session, so no cross-process coordination is needed here.
"""

import time

_WINDOW_SECONDS = 10
_last_call: dict[int, float] = {}


def allow(chat_id: int) -> bool:
    now = time.monotonic()
    last = _last_call.get(chat_id, 0.0)
    if now - last < _WINDOW_SECONDS:
        return False
    _last_call[chat_id] = now
    return True
