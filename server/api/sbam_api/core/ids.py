"""RFC 9562 UUIDv7 ids (time-ordered). Python 3.12 has no uuid.uuid7."""

import secrets
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0


def uuid7() -> uuid.UUID:
    """A UUIDv7 that sorts after every id this process made before it.

    RFC 9562 §6.2 method 1: a 12-bit counter in rand_a orders ids within one millisecond. If the
    clock steps back, or the counter runs out, the timestamp is held at (or bumped past) the last
    one, so ordering never goes backwards because of clock skew.
    """
    global _last_ms, _counter
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms > _last_ms:
            _last_ms, _counter = ms, secrets.randbits(10)  # random start leaves room to count up
        else:
            _counter += 1
            if _counter > 0xFFF:
                _last_ms, _counter = _last_ms + 1, 0
        ms, counter = _last_ms, _counter
    value = (ms << 80) | (0x7 << 76) | (counter << 64) | (0b10 << 62) | secrets.randbits(62)
    return uuid.UUID(int=value)
