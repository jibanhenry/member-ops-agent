"""调用频率与额度保护：公开演示时防止有人刷接口、把每天的免费模型额度用光。"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import date


class RateLimiter:
    """每个访客（按 IP）每分钟最多 N 次。"""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            if len(self.hits) > 10000:  # 防止访客表无限增长
                self.hits = defaultdict(deque, {k: v for k, v in self.hits.items() if v})
            return True


class DailyBudget:
    """全站每天最多调用大模型的请求数，跨天自动清零。"""

    def __init__(self, limit: int):
        self.limit = limit
        self.day = date.today()
        self.used = 0
        self.lock = threading.Lock()

    def take(self) -> bool:
        if self.limit <= 0:
            return True
        with self.lock:
            if date.today() != self.day:
                self.day, self.used = date.today(), 0
            if self.used >= self.limit:
                return False
            self.used += 1
            return True

    def status(self) -> dict:
        return {"limit": self.limit, "used": self.used, "day": str(self.day)}
