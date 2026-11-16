"""调用频率与额度保护：公开演示时防止有人刷接口、把每天的免费模型额度用光。"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def beijing_today() -> date:
    """魔搭每天北京时间零点发放魔粒；服务器时钟可能是 UTC，统一按北京时间算"今天"。"""
    return datetime.now(BEIJING).date()


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
        self.day = beijing_today()
        self.used = 0
        self.exhausted_day: date | None = None  # 模型服务方的额度（如魔搭魔粒）当天已用完
        self.lock = threading.Lock()

    def _roll(self) -> None:
        if beijing_today() != self.day:
            self.day, self.used = beijing_today(), 0

    def take(self) -> bool:
        with self.lock:
            self._roll()
            if self.exhausted_day == self.day or (self.limit > 0 and self.used >= self.limit):
                return False
            self.used += 1
            return True

    def mark_exhausted(self) -> None:
        """模型服务方返回额度不足：当天不再调用，北京时间零点后自动恢复。"""
        with self.lock:
            self._roll()
            self.exhausted_day = self.day

    def status(self) -> dict:
        with self.lock:
            self._roll()
            return {"limit": self.limit, "used": self.used, "day": str(self.day),
                    "exhausted": self.exhausted_day == self.day or (self.limit > 0 and self.used >= self.limit)}
