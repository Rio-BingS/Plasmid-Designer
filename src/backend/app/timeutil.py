"""时间工具：统一的 UTC 当前时间

数据库 DateTime 列（SQLite / PostgreSQL TIMESTAMP WITHOUT TIME ZONE）
历来存的是"朴素 UTC"时间，比较时两侧必须同为朴素值，否则抛
TypeError。datetime.utcnow() 在 Python 3.12 起弃用，这里用时区感知的
now(timezone.utc) 取值后去掉 tzinfo，语义与旧写法完全一致。
"""

from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """当前 UTC 时间（朴素，不带 tzinfo），用于写入/比较数据库时间列"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
