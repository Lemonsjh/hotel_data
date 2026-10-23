from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta


MAX_BACKFILL_DAYS = 366


def same_day_last_year(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=monthrange(value.year - 1, value.month)[1])


def query_dates(
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    today: date | None = None,
) -> list[str]:
    """返回指定日期及其去年同期；PMS 日报页面一次仅支持一个营业日。"""
    latest = (today or date.today()) - timedelta(days=1)
    try:
        start = date.fromisoformat(start_date or end_date or latest.isoformat())
        end = date.fromisoformat(end_date or start_date or latest.isoformat())
    except ValueError as exc:
        raise ValueError("日期格式必须为 YYYY-MM-DD") from exc
    if end < start:
        raise ValueError("结束日期不能早于开始日期")
    if end > latest:
        raise ValueError(f"仅支持已完成营业日，最晚可选 {latest.isoformat()}")
    count = (end - start).days + 1
    if count > MAX_BACKFILL_DAYS:
        raise ValueError(f"单次最多补采 {MAX_BACKFILL_DAYS} 天，请分批执行")
    result: list[str] = []
    for offset in range(count):
        business_date = start + timedelta(days=offset)
        result.extend((business_date.isoformat(), same_day_last_year(business_date).isoformat()))
    return list(dict.fromkeys(result))
