"""Prefixed, time-sortable, monotonic ULIDs: '<prefix>_<26 Crockford base32 chars>'."""
from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    global _last_ms, _last_rand
    with _lock:
        ms = int(time.time() * 1000)
        if ms <= _last_ms:
            ms = _last_ms
            _last_rand += 1
        else:
            _last_rand = int.from_bytes(os.urandom(10), "big") >> 1  # leave headroom for +1
        _last_ms = ms
        value = (ms << 80) | (_last_rand & ((1 << 80) - 1))
    return f"{prefix}_{_encode(value, 26)}"
