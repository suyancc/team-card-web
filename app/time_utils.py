from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SQLITE_SHANGHAI_NOW = "datetime('now', '+8 hours')"


def shanghai_now_text() -> str:
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def shanghai_now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")
