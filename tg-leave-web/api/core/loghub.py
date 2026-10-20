# -*- coding: utf-8 -*-
"""日志中心：内存环形缓冲 + WebSocket 订阅者广播。"""
import asyncio
import time
from collections import deque

LOG_INFO = "info"
LOG_WARN = "warn"
LOG_ERROR = "error"
_LOG_LEVEL_TOKENS_WARN = ("⚠", "❓", "⏹", "⏳", "↻", "🚫", "⏭")
_LOG_LEVEL_TOKENS_ERR = ("❌", "⛔", "🔥")


def level_of(msg):
    if any(tok in (msg or "") for tok in _LOG_LEVEL_TOKENS_ERR):
        return LOG_ERROR
    if any(tok in (msg or "") for tok in _LOG_LEVEL_TOKENS_WARN):
        return LOG_WARN
    return LOG_INFO


class LogHub:
    def __init__(self, capacity=2000):
        self._buf = deque(maxlen=capacity)
        self._subscribers = set()
        self._lock = asyncio.Lock()

    def log(self, message, level=None, source="app"):
        level = level or level_of(message)
        entry = {
            "t": time.strftime("%H:%M:%S"),
            "ts": time.time(),
            "level": level,
            "source": source,
            "message": message,
        }
        self._buf.append(entry)
        # 同步广播给订阅者（WS）
        for q in list(self._subscribers):
            try:
                q.put_nowait(entry)
            except Exception:
                pass
        return entry

    def recent(self, limit=500):
        items = list(self._buf)
        return items[-limit:]

    def subscribe(self):
        q = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self._subscribers.discard(q)


# 全局单例
HUB = LogHub()
