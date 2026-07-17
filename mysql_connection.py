from __future__ import annotations

import time
from typing import Any


RETRYABLE_ERROR_CODES = {2003, 2006, 2013}


def connect_mysql(
    config: dict[str, Any],
    *,
    autocommit: bool = False,
    attempts: int = 3,
):
    """使用统一超时和递增退避建立 MySQL 连接。"""
    import pymysql

    options = dict(config)
    options.setdefault("connect_timeout", 12)
    options.setdefault("read_timeout", 30)
    options.setdefault("write_timeout", 30)
    options["autocommit"] = autocommit
    for attempt in range(1, attempts + 1):
        try:
            return pymysql.connect(**options)
        except pymysql.err.OperationalError as exc:
            code = exc.args[0] if exc.args else None
            if code not in RETRYABLE_ERROR_CODES or attempt == attempts:
                raise
            delay = 2 * attempt
            print(f"MySQL connection retry in {delay}s ({attempt}/{attempts}), error={code}")
            time.sleep(delay)
