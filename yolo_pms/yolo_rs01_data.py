#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""余乐通 RS01：客房收入日表（房费流水明细）。

一行 = 一条房费过账流水。数据源 T_PriceList，只取 F_RoomPriceOrOther='房价'，
消费、营销、洗涤、赔偿、积分奖励等非房费流水全部排除。

口径
  · 营业日    交易日减 5 小时取日期（余乐通营业日以 05:00 为界，与 jl01/jy01 一致）
  · 房晚      科目含「钟点」记 0.5（钟点房按半间夜，与经营报表一致）；
              科目含「增免」记 0（同一晚房费的追加/减免，不是独立间夜，
              按 1 记会让 30 天窗口虚增 3 房晚）；
              其余记 1
  · 零金额    F_TotalPrice=0 的流水是开房时的占位记录，不构成收入，跳过不写
  · 订单号    源库没有任何渠道订单号（见下），落 PMS 单号 T_RoomOrder.F_ID
  · 客户来源  F_GuestType / F_Memo 里的文字归一成 美团 / 携程 / 飞猪 / 去哪儿 /
              长租 / 会员 / 散客，原始价格方案名保留在 price_type

关于渠道订单号
  余乐通的 OTA 写入通路整条没有启用：F_Ota_Orderno 全库 0 条非空，
  f_ota_order_id 的 66029 条全是字符串 "0"，F_XyzOut_Orderid 全空，
  tp_ota_pay / tp_ota_pricelist / T_ThreeBookOrder / T_Platform_Sale 四张表 0 行。
  平台把渠道单号只记在文字里（「挂账:美团（OTA平台）」），取不到单号本身。
  因此 order_id 与 jd01 同为 PMS 单号，两表可直接关联；
  唯一性由 source_payment_id（流水 F_ID）保证，对账靠房号 + 营业日 + 金额。

采集范围：最近 30 个完整营业日，与其他 4 家 PMS 的 rs01 一致。
落库按营业日整段替换，历史在同一事务内按 pms_daily_days（默认 400 天）清理。

用法
  python yolo_rs01_data.py              # 采集并落库
  python yolo_rs01_data.py --dry-run    # 只取数不落库，验证映射与对账
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yolo_common import (  # noqa: E402
    SOURCE_PLATFORM,
    Config,
    YoloError,
    query,
    replace_snapshot,
    report_main,
    save_output,
)
from yolo_sql import RS01_QUERY, RS01_RECONCILE_QUERY, ROOM_TYPE_GROUP  # noqa: E402

SUMMARY_FILE = "yolo_rs01_summary.json"
TABLE = "rs01_room_revenue_daily"
# 与其他 4 家 PMS 的 rs01 保持一致
COLLECTION_DAYS = 30
# 余乐通营业日以 05:00 为界：05:00 之前的交易仍算前一个营业日
BUSINESS_DAY_START_HOUR = 5

COLUMNS = (
    "hotel_name", "hotel_id", "source_platform", "business_date", "room_no",
    "room_type_name", "room_type_id", "guest_name", "customer_source", "checkin_time",
    "checkout_time", "rack_rate", "price_type", "room_daily_price", "stay_type",
    "charge_subject", "room_nights", "room_fee", "operator_name", "order_id",
    "source_payment_id", "channel_unit_id", "snapshot_time",
)

# 渠道只以文字形式存在：价格方案名（F_GuestType）与挂账备注（F_Memo）。
# 先匹配 OTA，再匹配自有客源，顺序敏感——「散客享平日网络」含「网络」但不含平台名，
# 不会被误判成渠道；「平日赫程价」命中携程（赫程为携程旗下）。
CHANNEL_KEYWORDS = (
    ("美团", ("美团",)),
    ("携程", ("赫程", "携程")),
    ("飞猪", ("飞猪",)),
    ("去哪儿", ("去哪",)),
)
# 「增免房费」是同一晚房费的追加或减免，不产生独立间夜
NIGHTS_ZERO_KEYWORD = "增免"
# 「钟点房起始价」这类钟点房流水按半间夜折算
NIGHTS_HALF_KEYWORD = "钟点"


def report_window(today: date) -> list[date]:
    """最近 N 个完整营业日（昨天往前，不含今天）。

    当天要等次日 05:00 夜审后才写进源表，采不到；窗口保持 N 天重叠是为了自愈：
    某轮失败、停机或补账，下一轮会把这段重算一遍。
    """
    latest = today - timedelta(days=1)
    return [latest - timedelta(days=offset) for offset in reversed(range(COLLECTION_DAYS))]


def window_bounds(days: list[date]) -> tuple[datetime, datetime]:
    """营业日区间折算成交易日区间：05:00 起算，右端开区间。"""
    start = datetime.combine(days[0], datetime.min.time()) + timedelta(hours=BUSINESS_DAY_START_HOUR)
    end = datetime.combine(days[-1] + timedelta(days=1), datetime.min.time()) + timedelta(hours=BUSINESS_DAY_START_HOUR)
    return start, end


def to_decimal(value: str | None, label: str) -> Decimal:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return Decimal(0)
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise YoloError(f"房费流水 {label} 数值无效：{value!r}") from None
    if not result.is_finite():
        raise YoloError(f"房费流水 {label} 含非有限数值。")
    return result


def rounded(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def to_datetime(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def business_date_of(trade_time: datetime) -> date:
    """营业日 = 交易日减 5 小时后的日期。00:00~04:59 的交易归属前一天。"""
    return (trade_time - timedelta(hours=BUSINESS_DAY_START_HOUR)).date()


def room_nights_of(charge_subject: str, stay_type: str) -> Decimal:
    """按科目判间夜：增免调整不计间夜、钟点房半间、其余一晚。

    先判增免再判钟点：小时房订单也会产生「增免房费」，那是金额调整，
    不是又开了半间房。
    """
    if NIGHTS_ZERO_KEYWORD in charge_subject:
        return Decimal(0)
    if NIGHTS_HALF_KEYWORD in charge_subject or stay_type == "小时房":
        return Decimal("0.5")
    return Decimal(1)


def channel_of(guest_type: str, remarks: str) -> str:
    """把价格方案名与挂账备注归一成客户来源。

    识别不到时回落原价格方案名，而不是硬塞「其他」：
    price_type 已保留原值，这里再回落一次是为了不让自定义价格方案丢失信息。
    """
    text = f"{guest_type} {remarks}"
    for name, keywords in CHANNEL_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return name
    if "长租" in text:
        return "长租"
    if "会员" in text or "金卡" in text:
        return "会员"
    if "散客" in text or "上门" in text:
        return "散客"
    return guest_type.strip() or "未知"


def build_rows(records: list[dict[str, str]], config: Config, snapshot: datetime,
               days: list[date]) -> tuple[list[dict], dict]:
    """把房费流水整理成 rs01 行，并产出对账与质量统计。"""
    allowed = set(days)
    rows: list[dict] = []
    seen: set[str] = set()
    zero_amount = 0
    negative_rows = 0
    half_night_rows = 0
    adjust_rows = 0
    nights_by_day: dict[date, Decimal] = {}
    revenue_by_day: dict[date, Decimal] = {}
    channel_rows: dict[str, int] = {}
    missing_type: list[str] = []

    for record in records:
        payment_id = (record.get("payment_id") or "").strip()
        order_id = (record.get("order_id") or "").strip()
        trade_time = to_datetime(record.get("trade_time"))
        if not payment_id or not order_id or not trade_time:
            raise YoloError("房费流水缺少流水号、订单号或交易时间。")
        if payment_id in seen:
            raise YoloError(f"房费流水号重复：{payment_id}")
        seen.add(payment_id)

        day = business_date_of(trade_time)
        if day not in allowed:
            # 05:00 边界若算错，这里会立刻暴露，而不是静默落到窗口外
            raise YoloError(f"房费流水 {payment_id} 营业日 {day} 不在请求窗口内。")

        fee = to_decimal(record.get("room_fee"), "房费")
        if fee == 0:
            # 开房占位记录，金额为 0，不构成收入
            zero_amount += 1
            continue
        if fee < 0:
            negative_rows += 1

        charge_subject = (record.get("charge_subject") or "").strip()
        if not charge_subject:
            raise YoloError(f"房费流水 {payment_id} 缺少科目名称。")
        stay_type = (record.get("booking_type") or "").strip() or None
        guest_type = (record.get("guest_type") or "").strip()
        remarks = (record.get("remarks") or "").strip()

        nights = room_nights_of(charge_subject, stay_type or "")
        if nights == Decimal("0.5"):
            half_night_rows += 1
        elif nights == 0:
            adjust_rows += 1

        daily_price = to_decimal(record.get("daily_price"), "当日房价")
        room_type_name = (record.get("room_type_name") or "").strip()
        if not room_type_name:
            missing_type.append(payment_id)

        channel = channel_of(guest_type, remarks)
        channel_rows[channel] = channel_rows.get(channel, 0) + 1

        nights_by_day[day] = nights_by_day.get(day, Decimal(0)) + nights
        revenue_by_day[day] = revenue_by_day.get(day, Decimal(0)) + fee

        rows.append({
            "hotel_name": config.hotel_name,
            "hotel_id": config.hotel_id,
            "source_platform": SOURCE_PLATFORM,
            "business_date": day,
            "room_no": (record.get("room_no") or "").strip(),
            "room_type_name": room_type_name or None,
            "room_type_id": None,          # 等人工映射后由 room_type_enrichment 回填
            "guest_name": (record.get("guest_name") or "").strip() or None,
            "customer_source": channel,
            # 原始价格方案名（平日赫程价 / 散客享平日网络 / 长租 …）保留在 price_type，
            # customer_source 只放归一后的渠道，两者不重复
            "price_type": guest_type or None,
            "checkin_time": to_datetime(record.get("checkin_time")),
            "checkout_time": to_datetime(record.get("checkout_time")),
            "rack_rate": None,             # 余乐通没有门市价字段
            # 增免流水是金额调整，F_Price 记的是调整额而非房价，留空避免误读
            "room_daily_price": None if nights == 0 else rounded(daily_price),
            "stay_type": stay_type,
            "charge_subject": charge_subject,
            "room_nights": rounded(nights),
            "room_fee": rounded(fee),
            "operator_name": (record.get("operator_name") or "").strip() or None,
            "order_id": order_id,
            "source_payment_id": payment_id,
            "channel_unit_id": None,       # PMS 不提供 OTA 售卖单元
            "snapshot_time": snapshot,
        })

    stats = {
        "source_rows": len(records),
        "zero_amount_skipped": zero_amount,
        "negative_rows": negative_rows,
        "half_night_rows": half_night_rows,
        "adjustment_rows": adjust_rows,
        "nights_by_day": nights_by_day,
        "revenue_by_day": revenue_by_day,
        "channel_rows": channel_rows,
        "rows_without_room_type": missing_type,
    }
    return rows, stats


def reconcile(config: Config, days: list[date], stats: dict) -> dict:
    """与经营报表逐日对账。差额只提示、不修改落库数据。

    报表由夜审生成，流水来自账务，两者偶有不同步（例如结账重算后报表未刷新）。
    rs01 采信流水：订单总价能与流水合计对上的，说明是报表漏记。
    """
    start, end = window_bounds(days)
    records = query(
        RS01_RECONCILE_QUERY.format(
            caption=ROOM_TYPE_GROUP,
            start=start.strftime("%Y-%m-%d %H:%M:%S"),
            end=end.strftime("%Y-%m-%d %H:%M:%S"),
        ),
        config, timeout=180, required=("business_date", "room_nights", "room_revenue"),
    )
    report: dict[date, tuple[Decimal, Decimal]] = {}
    for record in records:
        raw = (record.get("business_date") or "").strip()
        if not raw:
            continue
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            raise YoloError(f"对账查询返回了无效营业日：{raw!r}") from None
        report[day] = (
            to_decimal(record.get("room_nights"), "报表房晚"),
            to_decimal(record.get("room_revenue"), "报表房费"),
        )

    diffs = []
    for day in days:
        ledger_nights = stats["nights_by_day"].get(day, Decimal(0))
        ledger_revenue = stats["revenue_by_day"].get(day, Decimal(0))
        report_nights, report_revenue = report.get(day, (Decimal(0), Decimal(0)))
        if ledger_nights != report_nights or ledger_revenue != report_revenue:
            diffs.append({
                "business_date": day.isoformat(),
                "ledger_nights": str(ledger_nights),
                "ledger_revenue": str(ledger_revenue),
                "report_nights": str(report_nights),
                "report_revenue": str(report_revenue),
                "nights_diff": str(ledger_nights - report_nights),
                "revenue_diff": str(ledger_revenue - report_revenue),
            })

    ledger_total_nights = sum(stats["nights_by_day"].values(), Decimal(0))
    ledger_total_revenue = sum(stats["revenue_by_day"].values(), Decimal(0))
    report_total_nights = sum((item[0] for item in report.values()), Decimal(0))
    report_total_revenue = sum((item[1] for item in report.values()), Decimal(0))
    return {
        "ledger_nights": str(ledger_total_nights),
        "ledger_revenue": str(ledger_total_revenue),
        "report_nights": str(report_total_nights),
        "report_revenue": str(report_total_revenue),
        "nights_diff": str(ledger_total_nights - report_total_nights),
        "revenue_diff": str(ledger_total_revenue - report_total_revenue),
        "diff_days": diffs,
    }


def collect(config: Config, snapshot: datetime) -> dict:
    days = report_window(snapshot.date())
    start, end = window_bounds(days)

    records = query(
        RS01_QUERY.format(
            start=start.strftime("%Y-%m-%d %H:%M:%S"),
            end=end.strftime("%Y-%m-%d %H:%M:%S"),
        ),
        config, timeout=300,
        required=("payment_id", "order_id", "charge_subject", "room_fee", "trade_time"),
    )
    if not records:
        raise YoloError(f"房费流水在 {days[0]}~{days[-1]} 无数据，拒绝写入空结果。")

    rows, stats = build_rows(records, config, snapshot, days)
    if not rows:
        raise YoloError("窗口内房费流水全为零金额占位，拒绝写入空结果。")

    recon = reconcile(config, days, stats)

    warnings = []
    if stats["rows_without_room_type"]:
        warnings.append(
            f"{len(stats['rows_without_room_type'])} 条房费流水关联不到房型，"
            f"room_type_name 留空：{', '.join(stats['rows_without_room_type'][:5])}"
        )
    if recon["revenue_diff"] not in ("0", "0.00"):
        warnings.append(
            f"流水与经营报表不一致：房晚 {recon['nights_diff']}、"
            f"房费 {recon['revenue_diff']}，涉及 {len(recon['diff_days'])} 个营业日，"
            "已在 summary 的 reconciliation 中列明"
        )

    dry_run = "--dry-run" in sys.argv
    written = 0 if dry_run else replace_snapshot(
        TABLE, COLUMNS, rows, config, tuple(days),
        unique_key="order_id,business_date,room_no,charge_subject,source_payment_id",
    )

    for message in warnings:
        print(f"WARN {message}")
    summary = {
        "report": "rs01",
        "business_date": days[-1].isoformat(),
        "period_start": days[0].isoformat(),
        "period_end": days[-1].isoformat(),
        "hotel_id": config.hotel_id,
        "source_platform": SOURCE_PLATFORM,
        "snapshot_time": snapshot.isoformat(),
        "row_count": len(rows),
        "written": written,
        "dry_run": dry_run,
        "warnings": warnings,
        "source_rows": stats["source_rows"],
        "zero_amount_skipped": stats["zero_amount_skipped"],
        "negative_rows": stats["negative_rows"],
        "half_night_rows": stats["half_night_rows"],
        "adjustment_rows": stats["adjustment_rows"],
        "channel_rows": stats["channel_rows"],
        "reconciliation": recon,
        "basis": "源表 T_PriceList 房费流水（F_RoomPriceOrOther=房价）；营业日=交易日-5h；"
                 "房晚 钟点0.5/增免0/其余1；零金额占位跳过；"
                 "order_id=PMS 单号（源库无渠道订单号），customer_source 由价格方案名与挂账备注归一",
    }
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("rs01", collect)


if __name__ == "__main__":
    raise SystemExit(main())
