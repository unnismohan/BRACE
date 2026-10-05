"""Password policy and bounded, process-local login throttling."""
from collections import OrderedDict
from threading import Lock
import time

from fastapi import HTTPException


def validate_password(password):
    if len(password) < 12:
        raise HTTPException(400, "Use a password with at least 12 characters")
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(400, "Password must be at most 72 UTF-8 bytes")


class LoginLimiter:
    """Bounded sliding window; rate limit by peer address, never trust forwarded headers."""
    def __init__(self, attempts=10, window=60, capacity=10000, clock=time.monotonic):
        self.attempts, self.window, self.capacity = attempts, window, capacity
        self.clock = clock
        self.entries = OrderedDict()
        self.lock = Lock()

    def check(self, peer):
        now = self.clock()
        with self.lock:
            recent = [stamp for stamp in self.entries.pop(peer, []) if now - stamp < self.window]
            self.entries[peer] = recent
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)
            if len(recent) >= self.attempts:
                retry = max(1, int(self.window - (now - recent[0])) + 1)
                raise HTTPException(429, "Too many login attempts. Try again shortly.", headers={"Retry-After": str(retry)})
            recent.append(now)


login_limiter = LoginLimiter()
