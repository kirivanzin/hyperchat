"""Requests per caller, per minute, in this process.

The bound exists because an authenticated caller can otherwise spend the month: the allowlist says
*who* may call, not *how often*, and the provider charges per call.

**The state is in-process, and that is the honest limit of this.** It is per instance, so two
instances each allow the full rate; and it is in memory, so a scale-to-zero forgets the window. That
is correct for one instance and wrong the moment there is more than one, which is why it is written
here rather than discovered at the invoice. A shared counter would be a Redis or a Cloud Spanner
round trip on the request path, and that is its own ticket, not a line in this file.

Fixed window rather than a token bucket: a caller gets `per_minute` requests in each wall-clock
minute, and `Retry-After` says how long until the next one. A bucket smooths the boundary and needs
its own argument for a burst size; a window is the thing the setting names.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

WINDOW_SECONDS = 60.0


@dataclass
class RateLimiter:
    """A fixed window per subject. `check` returns `None` when allowed, or the seconds to wait."""

    per_minute: int
    # Injected so a test can move the clock instead of sleeping for a minute.
    clock: object = field(default=time.monotonic)
    _windows: dict[str, tuple[float, int]] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self, subject: str) -> int | None:
        """Count one request against `subject`, or say how long until it may ask again."""
        now = self.clock()
        with self._lock:
            started, used = self._windows.get(subject, (now, 0))
            if now - started >= WINDOW_SECONDS:
                started, used = now, 0
            if used >= self.per_minute:
                # Ceiling, and never zero: `Retry-After: 0` says "ask again now", which the caller
                # would, and be refused again.
                return max(1, math.ceil(WINDOW_SECONDS - (now - started)))
            self._windows[subject] = (started, used + 1)
            return None
