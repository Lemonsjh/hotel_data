from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from ctrip_config import COOKIE, DEFAULT_HOTEL_NAME, EXTRA_HEADERS, USER_AGENT
from ctrip_flow_conversion_data import FlowBrowserClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_table


API_URL = "https://ebooking.ctrip.com/restapi/soa2/24267/getInlandRightsRegistered"
TABLE_NAME = "ctrip_ota_joined_rights"
ALL_ROOM_TYPE_RIGHTS = {"FCN", "TQB", "YCT"}
HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "right_type_id", "right_type", "right_name",
    "applicable_room_types", "invalid_dates", "rights_rules", "stock_use_conditions", "right_status",
    "snapshot_time",
]


def require_hotel_id() -> str:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty; configure the Ctrip internal hotel ID")
    return hotel_id


def text_list(values: Any) -> str:
    result: list[str] = []
    if not isinstance(values, list):
        return ""
    for value in values:
        if isinstance(value, dict):
            value = value.get("name")
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return "; ".join(result)


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("Ctrip joined-rights response is not an object")
    status = payload.get("resStatus") or {}
    if status and status.get("rcode") != 200:
        raise RuntimeError(f"Ctrip joined-rights request failed: {status.get('rmsg') or 'unknown error'}")
    if not isinstance(payload.get("registeredConditions"), list):
        raise RuntimeError("Ctrip joined-rights response is missing registeredConditions")
    return payload


def request_rights(client: FlowBrowserClient) -> dict[str, Any]:
    return validate_payload(client.post_json(API_URL, {"cipher": None}))


def request_rights_with_cookie() -> dict[str, Any]:
    if not COOKIE:
        raise RuntimeError("Ctrip browser profile is busy and Ctrip Cookie is not configured")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://ebooking.ctrip.com",
        "Referer": "https://ebooking.ctrip.com/promotion/rightsInland/home?microJump=true",
        "Cookie": COOKIE,
    }
    headers.update(EXTRA_HEADERS)
    response = requests.post(API_URL, json={"cipher": None}, headers=headers, timeout=30)
    response.raise_for_status()
    try:
        return validate_payload(response.json())
    except ValueError as exc:
        raise RuntimeError(f"Ctrip joined-rights response is not JSON, HTTP={response.status_code}") from exc


def build_rows(payload: dict[str, Any], captured_at: datetime) -> list[list[Any]]:
    hotel_id = require_hotel_id()
    rows: list[list[Any]] = []
    for item in payload.get("registeredConditions") or []:
        if not isinstance(item, dict):
            continue
        right_type = str(item.get("rightType") or "").strip()
        right_name = str(item.get("rightName") or "").strip()
        if not right_type or not right_name:
            continue
        room_types = text_list(item.get("roomTypes"))
        room_extras = text_list(item.get("roomTypeExtras"))
        scope = "; ".join(value for value in (room_types, room_extras) if value)
        if not scope and right_type in ALL_ROOM_TYPE_RIGHTS:
            scope = "全房型参与"
        rows.append([
            hotel_id, DEFAULT_HOTEL_NAME, "ctrip", item.get("rightTypeId"), right_type, right_name,
            scope, str(item.get("invalidDates") or "").strip(), str(item.get("rightsRules") or "").strip(),
            str(item.get("stockUseConditions") or "").strip(), str(item.get("status") or "").strip(), captured_at,
        ])
    if not rows:
        raise RuntimeError("Ctrip joined-rights response contains no valid rights")
    return rows


def write_output(rows: list[list[Any]], captured_at: datetime) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "table_name": TABLE_NAME,
        "snapshot_time": captured_at.isoformat(sep=" ", timespec="seconds"),
        "row_count": len(rows),
        "rows": [
            {header: value.isoformat() if hasattr(value, "isoformat") else value for header, value in zip(HEADERS, row)}
            for row in rows
        ],
    }
    (OUTPUT_DIR / f"{TABLE_NAME}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    captured_at = datetime.now()
    try:
        with FlowBrowserClient() as client:
            payload = request_rights(client)
    except Exception as browser_error:
        print(f"Ctrip browser session unavailable ({type(browser_error).__name__}); trying configured Cookie session")
        payload = request_rights_with_cookie()
    rows = build_rows(payload, captured_at)
    write_output(rows, captured_at)
    sync_table(TABLE_NAME, HEADERS, rows)
    print(f"Ctrip joined-rights sync completed: rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
