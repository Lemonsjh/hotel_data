#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from fetch_room_type_forecast import (
    FORECAST_URL,
    ROOM_TYPES_URL,
    api_session,
    forecast_window,
    request_json,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT_DIR / "output" / "ROOM_STATUS.json"


def today_detail(details: list[dict[str, Any]], stay_date: str) -> dict[str, Any] | None:
    return next(
        (item for item in details if str(item.get("Date") or "").split(" ")[0] == stay_date),
        None,
    )


def fetch_room_type_hourly_status() -> bool:
    print("\n=== Fetching PMS hourly room status ===")
    session = api_session()
    if session is None:
        return False
    now = datetime.now()
    window = forecast_window(1)
    stay_date = now.date().isoformat()
    try:
        room_types = request_json(session, "GET", ROOM_TYPES_URL).get("Content") or []
        rows: list[dict[str, Any]] = []
        for index, room_type in enumerate(room_types, start=1):
            if not room_type.get("IsActive", True) or room_type.get("Virtual", False):
                continue
            name = str(room_type.get("RoomTypeName") or "").strip()
            source_id = str(room_type.get("Id") or "").strip()
            if not name or not source_id:
                continue
            content = request_json(
                session, "POST", FORECAST_URL, json={**window, "roomType": room_type}
            ).get("Content") or []
            detail = today_detail((content[0].get("Details") or []) if content else [], stay_date)
            if detail:
                rows.append(
                    {
                        "pms_room_type_id": source_id,
                        "room_type_name": name,
                        "total_rooms": content[0].get("TotalCount"),
                        "available_rooms": detail.get("AvailiableCount"),
                        "occupied_rooms": detail.get("OccupationCount"),
                        "overbooking_rooms": detail.get("OverbookingCount"),
                    }
                )
            print(f"Hourly room status [{index}/{len(room_types)}] {name}")
            time.sleep(0.2)
        if not rows:
            raise RuntimeError("hourly room status returned no valid room types")
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(
            json.dumps(
                {
                    "meta": {
                        "snapshot_time": now.isoformat(timespec="microseconds"),
                        "snapshot_hour": now.replace(minute=0, second=0, microsecond=0).isoformat(),
                        "stay_date": stay_date,
                    },
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Hourly room status saved: {len(rows)} room types")
        return True
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"Hourly room status fetch failed: {exc}")
        return False


if __name__ == "__main__":
    raise SystemExit(0 if fetch_room_type_hourly_status() else 1)
