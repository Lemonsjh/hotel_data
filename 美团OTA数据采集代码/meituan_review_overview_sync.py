from __future__ import annotations

from typing import Any

import pymysql

from ota_mysql_writer import DB_CONFIG


TABLE_NAME = "meituan_ota_review_overview"


def extract_overview_counts(payload: dict[str, Any]) -> dict[str, int]:
    data = payload.get("data") or {}
    values = {
        "total_review_count": data.get("total", data.get("poiTotal")),
        "negative_review_count": data.get("negativeNum"),
        "unreplied_review_count": data.get("unRepliedNum", data.get("mtUnRepliedNum")),
    }
    return {key: int(value) for key, value in values.items() if value is not None}


def sync_overview_counts(counts: dict[str, int], hotel_id: str) -> None:
    if not counts:
        return
    connection = pymysql.connect(**DB_CONFIG, autocommit=False)
    try:
        with connection.cursor() as cursor:
            if hotel_id:
                cursor.execute(
                    f"SELECT id FROM {TABLE_NAME} WHERE hotel_id=%s ORDER BY snapshot_time DESC, id DESC LIMIT 1",
                    (hotel_id,),
                )
            else:
                cursor.execute(f"SELECT id FROM {TABLE_NAME} ORDER BY snapshot_time DESC, id DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                assignments = ", ".join(f"{key}=%s" for key in counts)
                cursor.execute(
                    f"UPDATE {TABLE_NAME} SET {assignments} WHERE id=%s",
                    (*counts.values(), row[0]),
                )
            else:
                columns = ["snapshot_time", "channel_source", "hotel_id", *counts]
                placeholders = ["NOW()", "%s", "%s", *(["%s"] * len(counts))]
                cursor.execute(
                    f"INSERT INTO {TABLE_NAME} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                    ("美团", hotel_id, *counts.values()),
                )
        connection.commit()
        print(f"DB synced: {TABLE_NAME} counts={counts}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
