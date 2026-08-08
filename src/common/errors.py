"""
Transient-error classification + retry -- common layer.

Distinguishes "the network/broker hiccuped" (retry, and halt as DISCONNECT so
the verified self-healer may resume once connectivity is back) from "the code
is wrong" (halt as EXCEPTION, manual reset forever). Classification is
conservative: anything unrecognized is NOT transient.

Boundary: places orders NO.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")

# Module prefixes whose exceptions are network-shaped (requests/urllib3 stack,
# raw sockets/http). Matched against every class in the exception's MRO.
_TRANSIENT_MODULES = ("requests", "urllib3", "http.client", "socket", "ssl")

# HTTP statuses worth retrying: rate-limit and server-side failures.
_TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def is_transient_error(exc: BaseException) -> bool:
    """True when `exc` looks like a connectivity/availability fault."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    status = getattr(exc, "status_code", None)  # alpaca-py APIError carries this
    try:
        if status is not None and int(status) in _TRANSIENT_STATUSES:
            return True
    except (TypeError, ValueError):
        pass
    for klass in type(exc).__mro__:
        module = getattr(klass, "__module__", "") or ""
        if module.startswith(_TRANSIENT_MODULES):
            return True
    if exc.__cause__ is not None and exc.__cause__ is not exc:
        return is_transient_error(exc.__cause__)
    return False


def retry_transient(
    fn: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `fn`, retrying ONLY transient errors with exponential backoff
    (base_delay, 2x per retry). Non-transient errors and the final transient
    failure propagate unchanged so the caller's halt logic can classify them."""
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if not is_transient_error(exc):
                raise
            last = exc
            if attempt < attempts - 1:
                sleep(base_delay * (2 ** attempt))
    assert last is not None
    raise last
