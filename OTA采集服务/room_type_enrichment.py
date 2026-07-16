from __future__ import annotations

import argparse
import json
from typing import Any, Iterable

import pymysql

import price_tasks
import runner


TABLES = {
    "jd01_booking_detail": ("pms_byh", "exact"),
    "jd04_inhouse_extension": ("pms_byh", "exact"),
    "jy01_hotel_statistics_daily": ("pms_byh", "exact"),
    "jy03_hotel_statistics_month": ("pms_byh", "exact"),
    "jl01_room_type_performance_daily": ("pms_byh", "exact"),
    "jl02_hotel_performance_daily": ("pms_byh", "exact"),
    "jl11_room_type_classification": ("pms_byh", "exact"),
    "pms_room_type_forecast": ("pms_byh", "exact"),
    "pms_room_type_hourly_status": ("pms_byh", "exact"),
    "kf11_room_status_snapshot": ("pms_byh", "exact"),
    "rs01_room_revenue_daily": ("pms_byh", "exact"),
    "meituan_ota_goods_price_mapping": ("meituan", "product"),
    "ctrip_ota_goods_price_mapping": ("ctrip", "product"),
    "meituan_ota_activity_product_detail": ("meituan", "product"),
    "ctrip_ota_activity_product_detail": ("ctrip", "exact"),
    "meituan_ota_review_detail": ("meituan", "prefix"),
    "ctrip_ota_review_detail": ("ctrip", "prefix"),
}
TASK_TABLES = {
    "pms_fetch": (
        "jd01_booking_detail",
        "jd04_inhouse_extension",
        "jy01_hotel_statistics_daily",
        "jy03_hotel_statistics_month",
        "jl01_room_type_performance_daily",
        "jl02_hotel_performance_daily",
        "jl11_room_type_classification",
        "pms_room_type_forecast",
        "pms_room_type_hourly_status",
        "kf11_room_status_snapshot",
        "rs01_room_revenue_daily",
    ),
    "meituan_goods_price": ("meituan_ota_goods_price_mapping",),
    "ctrip_goods_price": ("ctrip_ota_goods_price_mapping",),
    "meituan_promotion": ("meituan_ota_activity_product_detail",),
    "ctrip_promotion": ("ctrip_ota_activity_product_detail",),
    "meituan_review_detail": ("meituan_ota_review_detail",),
    "ctrip_review_detail": ("ctrip_ota_review_detail",),
}
PRODUCT_PLATFORMS = {
    "meituan": ("美团", "meituan"),
    "ctrip": ("携程", "ctrip"),
}
INDEX_NAME = "idx_hotel_room_type_id"
DIMENSION_TABLES = {"jy01_hotel_statistics_daily", "jy03_hotel_statistics_month"}


def _name_column(table: str) -> str:
    return "dimension_name" if table in DIMENSION_TABLES else "room_type_name"


def _room_rows(table: str, alias: str = "") -> str:
    prefix = f"`{alias}`." if alias else ""
    return f" AND {prefix}`dimension_type`='房型'" if table in DIMENSION_TABLES else ""


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND TABLE_TYPE='BASE TABLE'
        """,
        (table,),
    )
    return bool(cur.fetchone()["count"])


def ensure_schema(settings: dict[str, Any], conn=None) -> dict[str, list[str]]:
    added = {"columns": [], "indexes": []}
    owns_connection = conn is None
    conn = conn or price_tasks.connection(settings)
    try:
        with conn.cursor() as cur:
            for table in TABLES:
                if not _table_exists(cur, table):
                    continue
                cur.execute(
                    """
                    SELECT COUNT(*) AS count FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
                      AND COLUMN_NAME='room_type_id'
                    """,
                    (table,),
                )
                if not cur.fetchone()["count"]:
                    after_column = _name_column(table)
                    cur.execute(
                        f"""
                        ALTER TABLE `{table}`
                        ADD COLUMN `room_type_id` VARCHAR(50) NULL
                        COMMENT '系统统一房型ID，未映射时为空'
                        AFTER `{after_column}`
                        """
                    )
                    added["columns"].append(table)
                cur.execute(
                    """
                    SELECT COUNT(*) AS count FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
                    """,
                    (table, INDEX_NAME),
                )
                if not cur.fetchone()["count"]:
                    cur.execute(
                        f"CREATE INDEX `{INDEX_NAME}` ON `{table}` (`hotel_id`,`room_type_id`)"
                    )
                    added["indexes"].append(table)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()
    return added


def _scope_sql(hotel_id: str, alias: str = "t") -> tuple[str, tuple[str, ...]]:
    if not hotel_id:
        return "", ()
    return f" AND BINARY `{alias}`.`hotel_id`=BINARY %s", (hotel_id,)


def _clear_ids(cur, table: str, hotel_id: str) -> int:
    scope, params = _scope_sql(hotel_id)
    cur.execute(
        f"UPDATE `{table}` t SET t.room_type_id=NULL WHERE t.room_type_id IS NOT NULL{scope}",
        params,
    )
    return cur.rowcount


def _update_products(cur, table: str, platform: str, hotel_id: str) -> int:
    labels = PRODUCT_PLATFORMS[platform]
    scope, scope_params = _scope_sql(hotel_id)
    cur.execute(
        f"""
        UPDATE `{table}` t
        JOIN hotel_room_type_mapping m
          ON BINARY t.hotel_id=BINARY m.hotel_id
         AND BINARY t.ota_product_id=BINARY m.source_product_id
         AND m.source_product_id<>''
         AND m.source_platform IN (%s,%s)
         AND m.is_active=1
        SET t.room_type_id=m.room_type_id
        WHERE (t.room_type_id IS NULL OR BINARY t.room_type_id<>BINARY m.room_type_id)
        {scope}
        """,
        (*labels, *scope_params),
    )
    return cur.rowcount


def _update_alias(
    cur, table: str, platform: str, match_mode: str, hotel_id: str
) -> int:
    name_column = _name_column(table)
    room_rows = _room_rows(table)
    if platform == "pms_byh":
        mapping_name = "pms_room_type_name"
        mapping_filter = "source_product_id='' AND source_platform IN (%s,%s,%s,%s)"
        mapping_params = (*PRODUCT_PLATFORMS["meituan"], *PRODUCT_PLATFORMS["ctrip"])
    else:
        mapping_name = "source_room_type_name"
        product_labels = PRODUCT_PLATFORMS[platform]
        mapping_filter = """
        ((source_platform=%s AND source_product_id='')
          OR (source_platform IN (%s,%s) AND source_product_id<>''))
        """
        mapping_params = (platform, *product_labels)
    if match_mode == "prefix":
        alias_scope = " AND BINARY hotel_id=BINARY %s" if hotel_id else ""
        alias_params = (*mapping_params, *((hotel_id,) if hotel_id else ()))
        cur.execute(
            f"""
            SELECT hotel_id, `{mapping_name}` AS source_room_type_name,
                   MIN(room_type_id) AS room_type_id
            FROM hotel_room_type_mapping
            WHERE is_active=1 AND `{mapping_name}`<>''
              AND {mapping_filter}{alias_scope}
            GROUP BY hotel_id, `{mapping_name}`
            HAVING COUNT(DISTINCT room_type_id)=1
            ORDER BY CHAR_LENGTH(source_room_type_name) DESC
            """,
            alias_params,
        )
        aliases = list(cur.fetchall())
        changed = 0
        for alias in aliases:
            cur.execute(
                f"""
                UPDATE `{table}` SET room_type_id=%s
                WHERE room_type_id IS NULL
                  AND BINARY hotel_id=BINARY %s
                  AND BINARY LEFT(`{name_column}`, CHAR_LENGTH(%s))=BINARY %s
                  {room_rows}
                """,
                (
                    alias["room_type_id"],
                    alias["hotel_id"],
                    alias["source_room_type_name"],
                    alias["source_room_type_name"],
                ),
            )
            changed += cur.rowcount
        return changed
    scope, scope_params = _scope_sql(hotel_id)
    name_match = (
        f"BINARY t.`{name_column}`=BINARY m.source_room_type_name"
        if match_mode == "exact"
        else "0"
    )
    cur.execute(
        f"""
        UPDATE `{table}` t
        JOIN (
          SELECT hotel_id, `{mapping_name}` AS source_room_type_name,
                 MIN(room_type_id) AS room_type_id
          FROM hotel_room_type_mapping
          WHERE is_active=1 AND `{mapping_name}`<>'' AND {mapping_filter}
          GROUP BY hotel_id, `{mapping_name}`
          HAVING COUNT(DISTINCT room_type_id)=1
        ) m
          ON BINARY t.hotel_id=BINARY m.hotel_id
         AND {name_match}
        SET t.room_type_id=m.room_type_id
        WHERE t.room_type_id IS NULL
        {_room_rows(table, "t")}
        {scope}
        """,
        (*mapping_params, *scope_params),
    )
    return cur.rowcount


def _clear_mapping_scope(
    cur, table: str, change: dict[str, Any]
) -> int:
    platform, mode = TABLES[table]
    name_column = _name_column(table)
    hotel_ids = sorted(value for value in change["hotel_ids"] if value)
    room_ids = sorted(value for value in change["room_type_ids"] if value)
    aliases = sorted(value for value in change["aliases"].get(platform, ()) if value)
    if not hotel_ids or (not room_ids and not aliases):
        return 0
    hotel_sql = ",".join(["%s"] * len(hotel_ids))
    conditions: list[str] = []
    params: list[str] = list(hotel_ids)
    if room_ids:
        conditions.append("room_type_id IN (" + ",".join(["%s"] * len(room_ids)) + ")")
        params.extend(room_ids)
    for alias in aliases:
        if mode == "prefix":
            conditions.append(
                f"BINARY LEFT(`{name_column}`,CHAR_LENGTH(%s))=BINARY %s"
            )
            params.extend((alias, alias))
        else:
            conditions.append(f"BINARY `{name_column}`=BINARY %s")
            params.append(alias)
    cur.execute(
        f"""
        UPDATE `{table}` SET room_type_id=NULL
        WHERE hotel_id IN ({hotel_sql}) AND room_type_id IS NOT NULL
          {_room_rows(table)}
          AND ({" OR ".join(conditions)})
        """,
        tuple(params),
    )
    return cur.rowcount


def enrich_tables(
    settings: dict[str, Any],
    tables: Iterable[str] | None = None,
    *,
    reset: bool = False,
    hotel_id: str = "",
) -> dict[str, dict[str, int]]:
    selected = [table for table in (tables or TABLES) if table in TABLES]
    results: dict[str, dict[str, int]] = {}
    with price_tasks.connection(settings) as conn:
        ensure_schema(settings, conn)
        with conn.cursor() as cur:
            for table in selected:
                if not _table_exists(cur, table):
                    continue
                platform, mode = TABLES[table]
                cleared = _clear_ids(cur, table, hotel_id) if reset else 0
                product_rows = (
                    _update_products(cur, table, platform, hotel_id) if mode == "product" else 0
                )
                alias_mode = "exact" if mode == "product" else mode
                alias_rows = _update_alias(cur, table, platform, alias_mode, hotel_id)
                results[table] = {
                    "cleared": cleared,
                    "matched_by_product": product_rows,
                    "matched_by_name": alias_rows,
                }
        conn.commit()
    return results


def enrich_for_task(
    settings: dict[str, Any], task_name: str
) -> dict[str, dict[str, int]]:
    tables = TASK_TABLES.get(task_name)
    return enrich_tables(settings, tables) if tables else {}


def enrich_mapping_change(
    settings: dict[str, Any], change: dict[str, Any]
) -> dict[str, dict[str, int]]:
    ensure_schema(settings)
    cleared: dict[str, int] = {}
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        for table in TABLES:
            if _table_exists(cur, table):
                cleared[table] = _clear_mapping_scope(cur, table, change)
        conn.commit()
    results = enrich_tables(settings, hotel_id=str(change["target_hotel_id"]))
    for table, count in cleared.items():
        results.setdefault(
            table, {"cleared": 0, "matched_by_product": 0, "matched_by_name": 0}
        )
        results[table]["cleared"] = count
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Add and fill unified room_type_id fields.")
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--hotel-id", default="")
    args = parser.parse_args()
    settings = runner.load_settings()
    result: Any = ensure_schema(settings)
    if not args.migrate_only:
        result = enrich_tables(settings, reset=args.reset, hotel_id=args.hotel_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
