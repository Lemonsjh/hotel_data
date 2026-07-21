from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ctrip_config import DEFAULT_HOTEL_NAME
from ctrip_flow_conversion_data import FlowBrowserClient, number

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import sync_metric_history_table


API_URL = "https://ebooking.ctrip.com/toolcenter/api/psiV2/getHotelPsiV2?hostType=HE"
SUMMARY_TABLE = "ctrip_ota_psi_score"
METRIC_TABLE = "ctrip_ota_psi_metric"
SUMMARY_HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "business_date", "snapshot_time",
    "psi_total_score", "psi_basic_score", "psi_basic_score_max", "psi_reward_score",
    "psi_reward_score_max", "psi_deduction_score", "score_psi", "service_deduction_score",
    "integrity_deduction_score", "financial_deduction_score",
]
METRIC_HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "business_date", "metric_code", "metric_name",
    "metric_value", "metric_unit", "psi_score", "weight_pct", "competition_rank", "score_gap",
    "score_gap_unit", "period_start_date", "period_end_date", "snapshot_time",
]
METRICS = {
    1: ("historical_room_nights", "\u5386\u53f2\u95f4\u591c\u91cf", "historicalQuantity", "room_night"),
    2: ("historical_gmv", "\u5386\u53f2\u8425\u4e1a\u989d", "historicalGmv", "CNY"),
    9: ("historical_deal_rate", "\u5386\u53f2\u6210\u4ea4\u7387", "dealRate", "%"),
    3: ("instant_confirm_order_rate", "\u5373\u65f6\u786e\u8ba4\u8ba2\u5355\u5360\u6bd4", "confirmRate", "%"),
    4: ("consumer_value", "\u6d88\u8d39\u8005\u5b9e\u60e0\u5206", "pricing", "index"),
    5: ("room_status_good_rate", "\u623f\u6001\u826f\u597d\u5ea6", "roomStatus", "%"),
    6: ("review_competitiveness", "\u70b9\u8bc4\u7ade\u4e89\u6307\u6570", "comment", "index"),
    7: ("information_completeness", "\u4fe1\u606f\u5b8c\u6574\u5ea6", "info", "%"),
    8: ("cancellation_rate", "\u53ef\u53d6\u6d88\u7387", "cancelPolicy", "%"),
}


def require_hotel_id() -> str:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty; configure the Ctrip internal hotel ID")
    return hotel_id


def percentage(value: Any) -> int | float | None:
    parsed = number(value)
    if not isinstance(parsed, (int, float)):
        return None
    return round(parsed * 100 if 0 <= parsed <= 1 else parsed, 2)


def weight_percentage(value: Any) -> int | float | None:
    return number(str(value or "").strip().rstrip("%"))


def date_value(value: Any) -> date | None:
    text = str(value or "").strip().replace("/", "-")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def build_rows(payload: dict[str, Any], captured_at: datetime) -> tuple[list[Any], list[list[Any]]]:
    if payload.get("code") != 0:
        raise RuntimeError(f"Ctrip PSI request failed: {payload.get('message') or 'invalid response'}")
    data = payload.get("data") or {}
    summary = data.get("psiScoreBo") or {}
    detail = data.get("detailBo") or {}
    items = summary.get("basicScoreExtList") if isinstance(summary, dict) else None
    if not isinstance(summary, dict) or not isinstance(detail, dict) or not isinstance(items, list):
        raise RuntimeError("Ctrip PSI response is invalid")

    hotel_id = require_hotel_id()
    business_date = captured_at.date()
    total_score = number(summary.get("totalScore"))
    summary_row = [
        hotel_id, DEFAULT_HOTEL_NAME, "ctrip", business_date, captured_at,
        total_score, number(summary.get("basicScore")), number(summary.get("totalBasicScore")),
        number(summary.get("rewardScore")), number(summary.get("totalRewardScore")),
        number(summary.get("penaltyScore")), total_score, number(summary.get("punishScore")),
        number(summary.get("honestScore")), number(summary.get("financeScore")),
    ]
    metric_rows = []
    for item in items:
        if not isinstance(item, dict) or item.get("id") not in METRICS:
            continue
        metric_code, metric_name, detail_key, metric_unit = METRICS[item["id"]]
        raw_value = detail.get(detail_key)
        metric_rows.append([
            hotel_id, DEFAULT_HOTEL_NAME, "ctrip", business_date, metric_code, metric_name,
            percentage(raw_value) if metric_unit == "%" else number(raw_value), metric_unit,
            number(item.get("score")), weight_percentage(item.get("weight")), item.get("rank") or None,
            number(item.get("scoreGap")), item.get("scoreGapUnit") or None,
            date_value(item.get("startDate")), date_value(item.get("endDate")), captured_at,
        ])
    if not metric_rows:
        raise RuntimeError("Ctrip PSI response contains no diagnostic metrics")
    return summary_row, metric_rows


def main() -> int:
    captured_at = datetime.now()
    with FlowBrowserClient() as client:
        summary_row, metric_rows = build_rows(client.post_json(API_URL, {}), captured_at)
    sync_metric_history_table(
        SUMMARY_TABLE, SUMMARY_HEADERS, [summary_row], {"hotel_id", "platform_scope", "business_date"}, 30
    )
    sync_metric_history_table(
        METRIC_TABLE, METRIC_HEADERS, metric_rows,
        {"hotel_id", "platform_scope", "business_date", "metric_code"}, 30,
    )
    print(f"Ctrip PSI sync completed: diagnostic_metrics={len(metric_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
