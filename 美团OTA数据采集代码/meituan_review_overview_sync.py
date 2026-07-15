from __future__ import annotations

from typing import Any, Sequence

from ota_mysql_writer import connect_mysql


TABLE_NAME = "meituan_ota_review_overview"
DETAIL_COUNT_FIELDS = (
    "total_review_count",
    "unreplied_review_count",
    "negative_review_count",
)


def extract_overview_counts(payload: dict[str, Any]) -> dict[str, int]:
    data = payload.get("data") or {}
    values = {
        "total_review_count": data.get("total", data.get("poiTotal")),
        "negative_review_count": data.get("negativeNum"),
        "unreplied_review_count": data.get("unRepliedNum", data.get("mtUnRepliedNum")),
    }
    return {key: int(value) for key, value in values.items() if value not in (None, "")}


def sync_overview_counts(counts: dict[str, int], hotel_id: str, review_platform: str = "meituan") -> None:
    if not counts:
        return
    connection = connect_mysql(autocommit=False)
    try:
        with connection.cursor() as cursor:
            if hotel_id:
                cursor.execute(
                    f"""
                    SELECT id FROM {TABLE_NAME}
                    WHERE hotel_id=%s AND review_platform=%s
                    ORDER BY snapshot_time DESC, id DESC LIMIT 1
                    """,
                    (hotel_id, review_platform),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT id FROM {TABLE_NAME}
                    WHERE review_platform=%s
                    ORDER BY snapshot_time DESC, id DESC LIMIT 1
                    """,
                    (review_platform,),
                )
            row = cursor.fetchone()
            if row:
                assignments = ", ".join(f"`{key}`=%s" for key in counts)
                cursor.execute(
                    f"UPDATE {TABLE_NAME} SET {assignments} WHERE id=%s",
                    (*counts.values(), row[0]),
                )
            else:
                columns = ["snapshot_time", "channel_source", "review_platform", "hotel_id", *counts]
                placeholders = ["NOW()", "%s", "%s", "%s", *(["%s"] * len(counts))]
                cursor.execute(
                    f"INSERT INTO {TABLE_NAME} ({', '.join(f'`{column}`' for column in columns)}) "
                    f"VALUES ({', '.join(placeholders)})",
                    ("美团", review_platform, hotel_id, *counts.values()),
                )
        connection.commit()
        print(f"DB synced: {TABLE_NAME} platform={review_platform} counts={counts}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sync_overview_rows(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    """Replace overview rows while retaining detail-only count fields."""
    if not rows:
        return
    if not set(DETAIL_COUNT_FIELDS).issubset(headers):
        raise ValueError("Review overview headers are missing count fields")

    field_index = {field: headers.index(field) for field in DETAIL_COUNT_FIELDS}
    platform_index = headers.index("review_platform")
    hotel_index = headers.index("hotel_id")
    hotel_id = str(rows[0][hotel_index] or "")
    connection = connect_mysql(autocommit=False)
    try:
        with connection.cursor() as cursor:
            saved_counts = {
                platform: _load_detail_counts(cursor, hotel_id, platform)
                for platform in ("meituan", "dianping")
            }
            values = [list(row) for row in rows]
            for row in values:
                for field, value in saved_counts.get(row[platform_index], {}).items():
                    if row[field_index[field]] in (None, ""):
                        row[field_index[field]] = value

            columns = ", ".join(f"`{field}`" for field in headers)
            placeholders = ", ".join(["%s"] * len(headers))
            cursor.execute(f"DELETE FROM `{TABLE_NAME}`")
            cursor.executemany(
                f"INSERT INTO `{TABLE_NAME}` ({columns}) VALUES ({placeholders})",
                [tuple(None if value == "" else value for value in row) for row in values],
            )
        connection.commit()
        print(f"DB synced: {TABLE_NAME} rows={len(rows)}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _load_detail_counts(cursor: Any, hotel_id: str, review_platform: str) -> dict[str, int]:
    conditions = ["review_platform=%s", "total_review_count IS NOT NULL"]
    params: list[Any] = [review_platform]
    if hotel_id:
        conditions.append("hotel_id=%s")
        params.append(hotel_id)
    cursor.execute(
        f"SELECT {', '.join(DETAIL_COUNT_FIELDS)} FROM `{TABLE_NAME}` "
        f"WHERE {' AND '.join(conditions)} ORDER BY snapshot_time DESC, id DESC LIMIT 1",
        params,
    )
    row = cursor.fetchone()
    return dict(zip(DETAIL_COUNT_FIELDS, row)) if row else {}
