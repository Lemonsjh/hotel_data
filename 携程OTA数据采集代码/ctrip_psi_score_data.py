from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ctrip_config import DEFAULT_HOTEL_NAME
from ctrip_flow_conversion_data import FlowBrowserClient, number

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import sync_metric_history_table


API_URL = "https://ebooking.ctrip.com/restapi/soa2/24306/getPsiSummaryInfo"
TABLE_NAME = "ctrip_ota_psi_score"
HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "business_date", "snapshot_time",
    "psi_total_score", "psi_basic_score", "psi_basic_score_max", "psi_reward_score",
    "psi_reward_score_max", "psi_deduction_score", "score_psi", "psi_room_status_score",
    "psi_information_completeness_score", "psi_consumer_value_score",
]
ITEM_FIELDS = {
    5: "psi_room_status_score",
    7: "psi_information_completeness_score",
    4: "psi_consumer_value_score",
}


def item_scores(items: Any) -> dict[str, int | float | None]:
    scores = {field: None for field in ITEM_FIELDS.values()}
    if not isinstance(items, list):
        return scores
    for item in items:
        if isinstance(item, dict) and item.get("id") in ITEM_FIELDS:
            scores[ITEM_FIELDS[item["id"]]] = number(item.get("score"))
    return scores


def build_row(payload: dict[str, Any], captured_at: datetime) -> list[Any]:
    status = payload.get("resStatus") or {}
    summary = ((payload.get("data") or {}).get("psiSummaryInfoDto") or {})
    if status.get("rcode") != 200 or not isinstance(summary, dict) or not summary:
        raise RuntimeError(f"Ctrip PSI request failed: {status.get('rmsg') or 'invalid response'}")
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty; configure the Ctrip internal hotel ID")
    scores = item_scores(summary.get("basicScoreItemDtoList"))
    total_score = number(summary.get("totalScore"))
    return [
        hotel_id, DEFAULT_HOTEL_NAME, "ctrip", captured_at.date(), captured_at,
        total_score, number(summary.get("basicScore")), number(summary.get("totalBasicScore")),
        number(summary.get("rewardScore")), number(summary.get("totalRewardScore")),
        number(summary.get("penaltyScore")), total_score, scores["psi_room_status_score"],
        scores["psi_information_completeness_score"], scores["psi_consumer_value_score"],
    ]


def main() -> int:
    captured_at = datetime.now()
    with FlowBrowserClient() as client:
        row = build_row(client.post_json(API_URL, {}), captured_at)
    sync_metric_history_table(
        TABLE_NAME, HEADERS, [row], {"hotel_id", "platform_scope", "business_date"}, 30
    )
    print("Ctrip PSI score sync completed: rows=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
