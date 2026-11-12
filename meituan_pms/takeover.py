"""美团 PMS 接管起始营业日：该日及之后由美团覆盖其他 PMS 的同窗口数据。"""
from __future__ import annotations

import os
from datetime import date

ENV_KEY = "MEITUAN_PMS_TAKEOVER_DATE"


def takeover_date() -> date | None:
    """未配置时返回 None，保持各报表原有跨来源拒绝语义。"""
    raw = os.environ.get(ENV_KEY, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError("美团 PMS 接管起始营业日格式无效，应为 YYYY-MM-DD") from None
