#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""余乐通 JD02：客史 / 离店客人明细。

一行 = 一条「订单 × 客人」入住登记记录。数据源 T_OrderClient（办入住时逐位客人登记），
按离店营业日归档；同单多位客人各存一行。

口径
  · 营业日    离店时间减 5 小时取日期（余乐通营业日以 05:00 为界，与其它表一致）
  · 订单状态  只取「已结帐」「已换房」。取消预订（10495 条）与取消入住（641 条）
              从未入住，计入客史会让入住行为统计失真；已换房保留，客人确实住过。
  · 主客标记  F_BlLeadMan=1 记 main_checkin_person=1，否则 0；
              同单主客记录 ID 落 main_checkin_id，便于按单汇总。
  · 累计入住  total_checkin_count 按证件号统计该客人历史已结帐次数；
              证件号缺失时退到手机号，两者都没有则为 NULL。
  · 渠道      order_source 由 F_GuestType / F_Memo 归一（美团/携程/飞猪/去哪儿/
              长租/会员/散客），guest_source 保留 F_GuestType 原价格方案名。
  · 留空字段  room_type_id（待人工映射房型）、member_level_id / member_level
              （按证件号仅 16.5% 能匹配 T_Member，且其 F_MemberType 5706 条里
              5568 条是「长租」，属价格方案不是等级体系）、oneself_or_not、
              price_stakeholder_*（余乐通无挂账人字段）、check_out_tip、room_attr。

关于敏感信息
  余乐通是本地 SQL Server 直连，证件号与手机号在源库里是**明文**
  （如 510221198210243414 / 18584607088）。这与其他 PMS 不同：宝寓等 SaaS
  接口返回的本来就是脱敏值，建表注释「接口返回的证件号原值，通常已脱敏」
  对余乐通不成立。按业务确认，这里原样落库，不做变形。

采集范围：最近 30 个完整营业日；落库按营业日整段替换保证幂等，
历史在同一事务内按 60 天清理（对齐 pms_guest_storage.RETENTION_DAYS，
客史属敏感数据，不留长）。

用法
  python yolo_jd02_data.py              # 采集并落库
  python yolo_jd02_data.py --dry-run    # 只取数不落库
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yolo_common import (  # noqa: E402
    SOURCE_PLATFORM,
    Config,
    YoloError,
    mysql_connection,
    query,
    report_main,
    save_output,
)
from yolo_rs01_data import channel_of  # noqa: E402
from yolo_sql import JD02_QUERY  # noqa: E402

SUMMARY_FILE = "yolo_jd02_summary.json"
TABLE = "jd02_guest_stay_detail"
# 与其他表一致的 30 个完整营业日窗口
COLLECTION_DAYS = 30
# 客史属敏感数据，保留期刻意短于其它表的 400 天，与宝寓 jd02 的 60 天对齐
RETENTION_DAYS = 60
BUSINESS_DAY_START_HOUR = 5

COLUMNS = (
    "hotel_name", "hotel_id", "source_platform", "business_date", "order_id",
    "room_number", "room_type_name", "room_type_id", "customer_id", "customer_name",
    "id_card_number", "customer_mobile", "room_price", "arrival_time", "departure_time",
    "checkin_type", "order_source", "guest_source", "member_level_id", "member_level",
    "oneself_or_not", "price_stakeholder_name", "price_stakeholder_mobile", "order_memo",
    "check_out_tip", "checkin_status", "operator_name", "total_checkin_count",
    "room_attr", "main_checkin_id", "main_checkin_person", "snapshot_time",
)


def report_window(today: date) -> list[date]:
    """最近 N 个完整营业日（昨天往前，不含今天）。"""
    latest = today - timedelta(days=1)
    return [latest - timedelta(days=offset) for offset in reversed(range(COLLECTION_DAYS))]


def window_bounds(days: list[date]) -> tuple[datetime, datetime]:
    """营业日区间折算成离店时间区间：05:00 起算，右端开区间。"""
    start = datetime.combine(days[0], datetime.min.time()) + timedelta(hours=BUSINESS_DAY_START_HOUR)
    end = datetime.combine(days[-1] + timedelta(days=1), datetime.min.time()) + timedelta(
        hours=BUSINESS_DAY_START_HOUR)
    return start, end


def to_datetime(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def to_decimal(value: str | None, label: str) -> Decimal | None:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise YoloError(f"客史 {label} 数值无效：{value!r}") from None
    if not result.is_finite():
        raise YoloError(f"客史 {label} 含非有限数值。")
    return result


def to_int(value: str | None) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def text_of(value: str | None) -> str | None:
    return (value or "").strip() or None


def build_rows(records: list[dict[str, str]], config: Config, snapshot: datetime,
               days: list[date]) -> tuple[list[dict], dict]:
    """把客人登记记录整理成 jd02 行，并产出质量统计。"""
    allowed = set(days)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    status_rows: dict[str, int] = {}
    channel_rows: dict[str, int] = {}
    main_guests = sharers = 0
    with_card = with_phone = both_empty = 0
    no_count = 0
    off_window = 0

    for record in records:
        order_id = (record.get("order_id") or "").strip()
        guest_id = (record.get("guest_id") or "").strip()
        if not order_id or not guest_id:
            raise YoloError("客史查询返回了空单号或空客人 ID。")

        raw_date = (record.get("business_date") or "").strip()
        if not raw_date:
            raise YoloError(f"客史订单 {order_id} 缺少离店营业日。")
        business_day = date.fromisoformat(raw_date)
        if business_day not in allowed:
            # 查询窗口按离店时间取，跨 05:00 边界的记录会落到窗口外的营业日
            off_window += 1
            continue

        key = (order_id, guest_id)
        if key in seen:
            raise YoloError(f"客史同一订单出现重复客人记录：{order_id} / {guest_id}")
        seen.add(key)

        status = text_of(record.get("booking_status")) or ""
        status_rows[status] = status_rows.get(status, 0) + 1

        guest_type = text_of(record.get("guest_type")) or ""
        order_memo = text_of(record.get("order_memo")) or ""
        channel = channel_of(guest_type, order_memo)
        channel_rows[channel] = channel_rows.get(channel, 0) + 1

        id_card = text_of(record.get("id_card_number"))
        mobile = text_of(record.get("mobile"))
        if id_card:
            with_card += 1
        if mobile:
            with_phone += 1
        if not id_card and not mobile:
            both_empty += 1

        total_count = to_int(record.get("total_checkin_count"))
        if total_count is None:
            no_count += 1

        is_main = (record.get("is_main_guest") or "").strip()
        main_flag = 1 if is_main == "1" else 0
        if main_flag:
            main_guests += 1
        else:
            sharers += 1

        rows.append({
            "hotel_name": config.hotel_name,
            "hotel_id": config.hotel_id,
            "source_platform": SOURCE_PLATFORM,
            "business_date": business_day,
            "order_id": order_id,
            "room_number": text_of(record.get("room_no")),
            "room_type_name": text_of(record.get("room_type_name")),
            "room_type_id": None,
            "customer_id": guest_id,
            "customer_name": text_of(record.get("guest_name")),
            # 源库明文，按业务要求原样落库，不脱敏
            "id_card_number": id_card,
            "customer_mobile": mobile,
            "room_price": to_decimal(record.get("room_price"), "房价"),
            "arrival_time": to_datetime(record.get("arrival_time")),
            "departure_time": to_datetime(record.get("departure_time")),
            "checkin_type": text_of(record.get("booking_type")),
            "order_source": channel,
            "guest_source": guest_type or None,
            "member_level_id": None,
            "member_level": None,
            "oneself_or_not": None,
            "price_stakeholder_name": None,
            "price_stakeholder_mobile": None,
            "order_memo": order_memo or None,
            "check_out_tip": None,
            "checkin_status": status or None,
            "operator_name": text_of(record.get("operator_name")),
            "total_checkin_count": total_count,
            "room_attr": None,
            "main_checkin_id": text_of(record.get("main_guest_id")),
            "main_checkin_person": main_flag,
            "snapshot_time": snapshot,
        })

    stats = {
        "status_rows": status_rows,
        "channel_rows": channel_rows,
        "main_guest_rows": main_guests,
        "sharer_rows": sharers,
        "with_id_card": with_card,
        "with_mobile": with_phone,
        "both_empty": both_empty,
        "missing_checkin_count": no_count,
        "skipped_off_window": off_window,
    }
    return rows, stats


def sync_rows(rows: list[dict], config: Config, days: list[date]) -> int:
    """按营业日整段替换：先删窗口内旧数据再写入，保证重跑幂等。

    窗口外历史在同一事务内按 RETENTION_DAYS（60 天）清理。
    jd02 不接入 pms_history_retention 的原因：那张表按 data_retention 配置
    （默认 400 天）清理，而客史刻意只留 60 天，走配置容易被误改。
    """
    if not rows:
        raise YoloError("客史明细为空，已保留目标表原样。")

    hotel_id = config.hotel_id
    stamp = rows[0]["snapshot_time"]
    if any(row["hotel_id"] != hotel_id or row["source_platform"] != SOURCE_PLATFORM
           or row["snapshot_time"] != stamp for row in rows):
        raise YoloError("客史批次内酒店、来源或采集时间不一致。")

    marks = ", ".join(["%s"] * len(days))
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    column_sql = ", ".join(f"`{name}`" for name in COLUMNS)
    cutoff = stamp.date() - timedelta(days=RETENTION_DAYS)

    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT MAX(snapshot_time) FROM `{TABLE}` "
                f"WHERE hotel_id=%s AND source_platform=%s AND business_date IN ({marks})",
                (hotel_id, SOURCE_PLATFORM, *days),
            )
            existing = cursor.fetchone()
            if existing and existing[0] and existing[0] > stamp:
                raise YoloError(f"{TABLE} 已有更新批次，拒绝旧快照覆盖。")

            cursor.execute(
                f"DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s "
                f"AND business_date IN ({marks})",
                (hotel_id, SOURCE_PLATFORM, *days),
            )
            cursor.executemany(
                f"INSERT INTO `{TABLE}` ({column_sql}) VALUES ({placeholders})",
                [tuple(row[name] for name in COLUMNS) for row in rows],
            )
            cursor.execute(
                f"DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s "
                f"AND business_date < %s",
                (hotel_id, SOURCE_PLATFORM, cutoff),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


def collect(config: Config, snapshot: datetime) -> dict:
    days = report_window(snapshot.date())
    start, end = window_bounds(days)

    records = query(
        JD02_QUERY.format(start=start.strftime("%Y-%m-%d %H:%M:%S"),
                          end=end.strftime("%Y-%m-%d %H:%M:%S")),
        config, timeout=240, required=("guest_id", "order_id", "business_date", "arrival_time"),
    )
    rows, stats = build_rows(records, config, snapshot, days)

    dry_run = "--dry-run" in sys.argv
    written = 0 if dry_run or not rows else sync_rows(rows, config, days)

    summary = {
        "report": "jd02",
        "window": {"departure_from": start.isoformat(), "departure_to": end.isoformat()},
        "hotel_id": config.hotel_id,
        "source_platform": SOURCE_PLATFORM,
        "snapshot_time": snapshot.isoformat(),
        "retention_days": RETENTION_DAYS,
        "row_count": len(rows),
        "source_rows": len(records),
        "written": written,
        "dry_run": dry_run,
        "business_date": days[-1].isoformat(),
        **stats,
    }
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("jd02", collect)


if __name__ == "__main__":
    raise SystemExit(main())
