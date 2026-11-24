from __future__ import annotations

import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


ROLE_LEVEL = {"viewer": 0, "analyst": 1, "operator": 2, "approver": 3, "admin": 4}


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str


def configured_keys() -> list[tuple[str, str, str]]:
    values = []
    legacy = os.getenv("TRADEPILOT_API_KEY", "").strip()
    if legacy and legacy != "replace-with-a-long-random-secret":
        values.append((legacy, "legacy-admin", "admin"))
    for role in ROLE_LEVEL:
        value = os.getenv(f"TRADEPILOT_{role.upper()}_KEY", "").strip()
        if value:
            values.append((value, f"{role}-api-client", role))
    return values


def authenticate(token: str) -> Principal | None:
    for expected, subject, role in configured_keys():
        if secrets.compare_digest(token.encode(), expected.encode()):
            return Principal(subject, role)
    return None


def role_allows(actual: str, required: str) -> bool:
    return ROLE_LEVEL.get(actual, -1) >= ROLE_LEVEL[required]


class RateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.windows = defaultdict(deque)
        self.redis = None
        url = os.getenv("REDIS_URL", "").strip()
        if url:
            try:
                import redis

                self.redis = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
                self.redis.ping()
            except Exception:
                self.redis = None

    @property
    def mode(self) -> str:
        return "redis_distributed" if self.redis else "process_local"

    def allow(self, identity: str, limit: int) -> tuple[bool, int]:
        if self.redis:
            bucket = int(time.time() // 60)
            key = f"tradepilot:rate:{identity}:{bucket}"
            count = self.redis.incr(key)
            if count == 1:
                self.redis.expire(key, 65)
            return count <= limit, max(1, 60 - int(time.time() % 60))
        now = time.monotonic()
        with self.lock:
            window = self.windows[identity]
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= limit:
                return False, max(1, int(60 - (now - window[0])))
            window.append(now)
        return True, 0

    def clear_local(self) -> None:
        with self.lock:
            self.windows.clear()


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = defaultdict(int)
        self.errors = defaultdict(int)
        self.total_duration = defaultdict(float)

    def observe(self, method: str, path: str, status: int, duration: float) -> None:
        key = (method, path)
        with self.lock:
            self.requests[key] += 1
            self.total_duration[key] += duration
            if status >= 500:
                self.errors[key] += 1

    def prometheus(self) -> str:
        lines = [
            "# HELP tradepilot_http_requests_total HTTP requests.",
            "# TYPE tradepilot_http_requests_total counter",
        ]
        with self.lock:
            for (method, path), count in sorted(self.requests.items()):
                labels = f'method="{method}",path="{path}"'
                lines.append(f"tradepilot_http_requests_total{{{labels}}} {count}")
                lines.append(f"tradepilot_http_errors_total{{{labels}}} {self.errors[(method, path)]}")
                lines.append(f"tradepilot_http_duration_seconds_sum{{{labels}}} {self.total_duration[(method, path)]:.6f}")
        return "\n".join(lines) + "\n"
