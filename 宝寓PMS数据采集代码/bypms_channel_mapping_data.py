from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql
import requests


MAPPING_URL = os.environ.get(
    "BYPMS_CHANNEL_MAPPING_URL", "https://pms-api.bypms.cn/channel/api/v1/channel-unit/list"
)
COOKIE = os.environ.get("BYPMS_COOKIE", "").strip()
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()
HOTEL_NAME = os.environ.get("BYPMS_HOTEL_NAME", "").strip()
TIMEOUT_SECONDS = int(os.environ.get("BYPMS_TIMEOUT_SECONDS", "30"))
TABLE_NAME = "bypms_channel_unit_mapping_snapshot"
OUTPUT_DIR = Path(os.environ.get("HOTEL_OTA_OUTPUT_DIR", "OTA数据"))
CHANNELS = ("Meituan", "Ctrip", "Alitrip", "Dylife")


def mysql_config() -> dict[str, Any]:
    return {
        "host": os.environ.get("MYSQL_HOST") or os.environ.get("HOTEL_OTA_MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT") or os.environ.get("HOTEL_OTA_MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER") or os.environ.get("HOTEL_OTA_MYSQL_USER", ""),
        "password": os.environ.get("MYSQL_PASSWORD") or os.environ.get("HOTEL_OTA_MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE") or os.environ.get("HOTEL_OTA_MYSQL_DATABASE", ""),
        "charset": "utf8mb4",
    }


def fetch_channel_units(channel: str, session: requests.Session) -> list[dict[str, Any]]:
    response = session.get(
        MAPPING_URL,
        params={"channel": channel, "countLimit": 0},
        headers={
            "Accept": "application/json, text/plain, */*",
            "Cookie": COOKIE,
            "Referer": "https://www.bypms.cn/console/state/",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    units = (payload.get("data") or {}).get("channelUnits")
    if payload.get("state") != 0 or not isinstance(units, list):
        raise RuntimeError(f"ByPMS channel mapping API failed: {channel}")
    return [item for item in units if isinstance(item, dict)]


def build_rows(channel: str, units: list[dict[str, Any]], snapshot_time: datetime) -> list[dict[str, Any]]:
    snapshot_hour = snapshot_time.replace(minute=0, second=0, microsecond=0)
    rows = []
    for unit in units:
        unit_id = str(unit.get("channelUnitId") or "").strip()
        if not unit_id:
            continue
        relations = unit.get("relationList") if isinstance(unit.get("relationList"), list) else []
        relations = [item for item in relations if isinstance(item, dict)] or [{}]
        for relation in relations:
            rows.append(
                {
                    "hotel_id": HOTEL_ID,
                    "hotel_name": HOTEL_NAME,
                    "snapshot_time": snapshot_time,
                    "snapshot_hour": snapshot_hour,
                    "channel": str(unit.get("channel") or channel).strip(),
                    "channel_name": str(unit.get("channelName") or "").strip(),
                    "channel_unit_id": unit_id,
                    "channel_unit_name": str(unit.get("channelUnitName") or "").strip(),
                    "channel_unit_state": str(unit.get("state") or "").strip(),
                    "relation_type": str(unit.get("relationType") or "").strip(),
                    "relation_id": str(relation.get("id") or "").strip(),
                    "bypms_room_type_name": str(relation.get("name") or "").strip(),
                    "is_base_relation": 1 if relation.get("baseRelation") in {1, "1", True} else 0,
                    "relation_count": len(relations) if relations != [{}] else 0,
                }
            )
    return rows


def sync_mysql(rows: list[dict[str, Any]], snapshot_hour: datetime, connection=None) -> None:
    owns_connection = connection is None
    connection = connection or pymysql.connect(**mysql_config())
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM `{TABLE_NAME}` WHERE hotel_id=%s AND snapshot_hour=%s", (HOTEL_ID, snapshot_hour))
            if rows:
                cursor.executemany(
                    f"""
                    INSERT INTO `{TABLE_NAME}` (
                        hotel_id, hotel_name, snapshot_time, snapshot_hour, channel, channel_name,
                        channel_unit_id, channel_unit_name, channel_unit_state, relation_type,
                        relation_id, bypms_room_type_name, is_base_relation, relation_count
                    ) VALUES (
                        %(hotel_id)s, %(hotel_name)s, %(snapshot_time)s, %(snapshot_hour)s,
                        %(channel)s, %(channel_name)s, %(channel_unit_id)s, %(channel_unit_name)s,
                        %(channel_unit_state)s, %(relation_type)s, %(relation_id)s,
                        %(bypms_room_type_name)s, %(is_base_relation)s, %(relation_count)s
                    )
                    """,
                    rows,
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def main() -> int:
    if not HOTEL_ID or not COOKIE:
        raise RuntimeError("HOTEL_ID or BYPMS_COOKIE is empty")
    snapshot_time = datetime.now()
    with requests.Session() as session:
        rows = [row for channel in CHANNELS for row in build_rows(channel, fetch_channel_units(channel, session), snapshot_time)]
    sync_mysql(rows, snapshot_time.replace(minute=0, second=0, microsecond=0))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "bypms_channel_mapping_summary.json").write_text(
        json.dumps({"snapshot_time": snapshot_time.isoformat(timespec="seconds"), "record_count": len(rows)}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"ByPMS channel mapping synced: rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
