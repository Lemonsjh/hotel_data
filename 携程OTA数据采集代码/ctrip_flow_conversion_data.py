from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from ctrip_config import DEFAULT_HOTEL_NAME

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_table


API_URL = "https://ebooking.ctrip.com/datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1"
SCAN_FLOW_API_URL = "https://ebooking.ctrip.com/datacenter/api/inland/marketanalysis/flowanalysis/queryScanFlowDetailsV2"
COMPETITION_API_URL = "https://ebooking.ctrip.com/restapi/soa2/24588/getFlowData"
FLOW_HOME_URL = "https://ebooking.ctrip.com/home/mainland"
TABLE_NAME = "ctrip_ota_flow_conversion_30d"
DATE_RANGE = 30
PLATFORMS = {"ctrip": "Ctrip", "qunar": "Qunar"}
HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "ota_hotel_id",
    "business_date", "period_start_date", "period_end_date", "snapshot_time",
    "app_visitors", "peer_app_visitors",
    "list_exposure", "detail_exposure", "exposure_to_detail_rate_pct",
    "order_filling_count", "order_submit_count", "detail_to_order_rate_pct",
    "order_to_submit_rate_pct",
    "peer_list_exposure", "peer_detail_exposure", "peer_exposure_to_detail_rate_pct",
    "peer_order_filling_count", "peer_order_submit_count", "peer_detail_to_order_rate_pct",
    "peer_order_to_submit_rate_pct",
    "list_exposure_peer_rank", "detail_exposure_peer_rank", "order_filling_peer_rank",
    "exposure_to_detail_rate_peer_rank", "detail_to_order_rate_peer_rank",
]


def number(value: Any) -> int | float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        parsed = float(value)
        return int(parsed) if parsed.is_integer() else parsed
    except (TypeError, ValueError):
        return None


def rate(numerator: Any, denominator: Any) -> float | None:
    numerator_value = number(numerator)
    denominator_value = number(denominator)
    if numerator_value is None or not denominator_value:
        return None
    return round(float(numerator_value) / float(denominator_value) * 100, 2)


class FlowBrowserClient:
    def __enter__(self) -> "FlowBrowserClient":
        local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        profile = local_app_data / "HotelAgent" / "browser_profiles" / "ctrip"
        self.playwright = sync_playwright().start()
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), channel="msedge", headless=True, no_viewport=True,
            locale="zh-CN", timezone_id="Asia/Shanghai",
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.goto(FLOW_HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        return self

    def __exit__(self, *_: Any) -> None:
        self.context.close()
        self.playwright.stop()

    def post_json(self, url: str, payload: dict[str, Any]) -> Any:
        result = self.page.evaluate(
            """async ({ url, payload }) => {
                const response = await fetch(url, {
                    method: 'POST', credentials: 'include',
                    headers: { 'Content-Type': 'application/json;charset=UTF-8' },
                    body: JSON.stringify(payload),
                });
                const contentType = response.headers.get('content-type') || '';
                return {
                    status: response.status,
                    contentType,
                    body: contentType.includes('application/json') ? await response.json() : null,
                };
            }""",
            {"url": url, "payload": payload},
        )
        if result["body"] is None:
            raise RuntimeError("Ctrip browser profile is not logged in or flow request was blocked")
        return result["body"]


def fetch_rows(client: FlowBrowserClient, platform: str, start: date, end: date) -> list[dict[str, Any]]:
    data = client.post_json(
        f"{API_URL}?hostType=Ebooking",
        {"platform": platform, "startDate": start.isoformat(), "endDate": end.isoformat(),
         "fingerPrintKeys": "", "spiderkey": "", "spiderVersion": "2.0"},
    )
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"{platform} flow response is invalid or empty")
    return [item for item in data if isinstance(item, dict)]


def fetch_app_visitors(client: FlowBrowserClient, platform: str, start: date, end: date, data_type: int) -> int:
    payload = client.post_json(
        f"{SCAN_FLOW_API_URL}?hostType=Ebooking",
        {"platform": platform, "channelType": "0", "startDate": start.isoformat(), "endDate": end.isoformat(),
         "dateDimension": "2", "dataType": data_type, "fingerPrintKeys": "", "spiderkey": "", "spiderVersion": "2.0"},
    )
    values = (payload.get("data") or {}).get("uvDataList")
    if payload.get("rcode") != 0 or not isinstance(values, list) or len(values) != DATE_RANGE:
        raise RuntimeError(f"{platform} scan-flow visitor data is incomplete (dataType={data_type})")
    return sum(number(value) or 0 for value in values)


def fetch_competition_ranks(client: FlowBrowserClient, platform_scope: str, start: date, end: date) -> dict[int, int]:
    payload = client.post_json(
        COMPETITION_API_URL,
        {"dateType": 5, "beginDate": start.isoformat(), "endDate": end.isoformat(),
         "ota": platform_scope, "cipher": {}, "header": {"platform": "WEB"}},
    )
    status = payload.get("resStatus") or {}
    if status.get("rcode") != 200:
        raise RuntimeError(f"{platform_scope} competition-flow request failed: {status.get('rmsg') or 'unknown error'}")
    items = (payload.get("data") or payload).get("dataList") or []
    ranks = {
        int(item["indexType"]): int(number(item["rankComp"]))
        for item in items
        if isinstance(item, dict) and item.get("indexType") in (6, 7, 8, 9, 10) and number(item.get("rankComp")) is not None
    }
    if len(ranks) != 5:
        raise RuntimeError(f"{platform_scope} competition-flow ranks are incomplete")
    return ranks


def build_row(
    source_rows: list[dict[str, Any]], platform_scope: str, start: date, end: date, captured_at: datetime,
    app_visitors: int, peer_app_visitors: int, competition_ranks: dict[int, int],
) -> list[Any]:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty; configure the Ctrip internal hotel ID")
    hotel_rows: dict[str, dict[str, Any]] = {}
    peer_rows: dict[str, dict[str, Any]] = {}
    for item in source_rows:
        source_hotel_id = number(item.get("hotelId"))
        target = peer_rows if source_hotel_id == -1 else hotel_rows
        target[str(item.get("date") or "")] = item

    if set(hotel_rows) != set(peer_rows) or len(hotel_rows) != DATE_RANGE:
        raise RuntimeError(f"{platform_scope} flow response does not contain a complete 30-day hotel/peer pair")

    def total(rows: dict[str, dict[str, Any]], field: str) -> int | float:
        return sum(number(item.get(field)) or 0 for item in rows.values())

    list_exposure = total(hotel_rows, "listExposure")
    detail_exposure = total(hotel_rows, "detailExposure")
    order_filling = total(hotel_rows, "orderFillingNum")
    order_submit = total(hotel_rows, "orderSubmitNum")
    peer_list_exposure = total(peer_rows, "listExposure")
    peer_detail_exposure = total(peer_rows, "detailExposure")
    peer_order_filling = total(peer_rows, "orderFillingNum")
    peer_order_submit = total(peer_rows, "orderSubmitNum")
    sample = next(iter(hotel_rows.values()))
    return [
        hotel_id, DEFAULT_HOTEL_NAME, platform_scope, number(sample.get("hotelId")), end,
        start, end, captured_at, app_visitors, peer_app_visitors, list_exposure, detail_exposure,
        rate(detail_exposure, list_exposure), order_filling, order_submit,
        rate(order_filling, detail_exposure), rate(order_submit, order_filling),
        peer_list_exposure, peer_detail_exposure, rate(peer_detail_exposure, peer_list_exposure),
        peer_order_filling, peer_order_submit, rate(peer_order_filling, peer_detail_exposure),
        rate(peer_order_submit, peer_order_filling),
        competition_ranks[6], competition_ranks[7], competition_ranks[8], competition_ranks[9], competition_ranks[10],
    ]


def write_output(rows: list[list[Any]], start: date, end: date, captured_at: datetime) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "table_name": TABLE_NAME,
        "period_start_date": start.isoformat(),
        "period_end_date": end.isoformat(),
        "snapshot_time": captured_at.isoformat(sep=" ", timespec="seconds"),
        "row_count": len(rows),
        "rows": [
            {header: value.isoformat() if hasattr(value, "isoformat") else value for header, value in zip(HEADERS, row)}
            for row in rows
        ],
    }
    (OUTPUT_DIR / f"{TABLE_NAME}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    captured_at = datetime.now()
    end = captured_at.date() - timedelta(days=1)
    start = end - timedelta(days=DATE_RANGE - 1)
    rows: list[list[Any]] = []
    with FlowBrowserClient() as client:
        for scope, platform in PLATFORMS.items():
            rows.append(
                build_row(
                    fetch_rows(client, platform, start, end), scope, start, end, captured_at,
                    fetch_app_visitors(client, platform, start, end, 0),
                    fetch_app_visitors(client, platform, start, end, 3),
                    fetch_competition_ranks(client, scope, start, end),
                )
            )
    write_output(rows, start, end, captured_at)
    sync_table(TABLE_NAME, HEADERS, rows)
    print(f"Ctrip 30-day flow conversion sync completed: rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
