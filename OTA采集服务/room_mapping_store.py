from __future__ import annotations

from typing import Any

import pymysql

from mapping_product_sync import sync_meituan_products
import price_tasks


PMS_PLATFORM = "pms_byh"
MEITUAN_PLATFORM = "meituan"
CTRIP_PLATFORM = "ctrip"
BASE_PLATFORMS = (PMS_PLATFORM, MEITUAN_PLATFORM, CTRIP_PLATFORM)
PRODUCT_PLATFORMS = {
    MEITUAN_PLATFORM: ("美团", "meituan"),
    CTRIP_PLATFORM: ("携程", "ctrip"),
}
OTA_BASE_LABELS = (*PRODUCT_PLATFORMS[MEITUAN_PLATFORM], *PRODUCT_PLATFORMS[CTRIP_PLATFORM])
FIELDS = (
    "hotel_id",
    "pms_hotel_name",
    "hotel_name",
    "ctrip_hotel_name",
    "room_type_id",
    "room_type_name",
    "pms_room_type_name",
    "meituan_room_type_name",
    "ctrip_room_type_name",
)
REQUIRED_FIELDS = tuple(
    name
    for name in FIELDS
    if name not in {"room_type_name", "ctrip_hotel_name", "ctrip_room_type_name"}
)
LABELS = {
    "hotel_id": "酒店 ID",
    "pms_hotel_name": "PMS 酒店名称",
    "hotel_name": "美团酒店名称",
    "ctrip_hotel_name": "携程酒店名称",
    "room_type_id": "统一房型 ID",
    "room_type_name": "统一房型名称",
    "pms_room_type_name": "PMS 房型名称",
    "meituan_room_type_name": "美团房型名称",
    "ctrip_room_type_name": "携程房型名称",
}


def defaults(settings: dict[str, Any]) -> dict[str, str]:
    return {
        "hotel_id": str(settings.get("hotel", {}).get("hotel_id", "")).strip(),
        "pms_hotel_name": str(settings.get("pms", {}).get("hotel_name", "")).strip(),
        "hotel_name": str(settings.get("meituan", {}).get("hotel_name", "")).strip(),
        "ctrip_hotel_name": str(settings.get("ctrip", {}).get("hotel_name", "")).strip(),
    }


def validate(data: dict[str, str]) -> str | None:
    missing = [LABELS[name] for name in REQUIRED_FIELDS if not data.get(name)]
    if missing:
        return "请填写：" + "、".join(missing)
    if data.get("ctrip_room_type_name") and not data.get("ctrip_hotel_name"):
        return "填写携程房型时，也需要填写携程酒店名称"
    return None


def _query_names(cur, sql: str, params: tuple[Any, ...] = ()) -> list[str]:
    try:
        cur.execute(sql, params)
        return [str(row["name"]).strip() for row in cur.fetchall() if str(row["name"]).strip()]
    except pymysql.MySQLError:
        return []


def room_options(
    settings: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str]]:
    hotel_id = str((settings.get("hotel") or {}).get("hotel_id") or "").strip()
    hotel_filter = " WHERE hotel_id=%s" if hotel_id else ""
    pms_params = (hotel_id, hotel_id) if hotel_id else ()
    pms_hotel_sql = f"""
    SELECT hotel_name AS name FROM (
        SELECT hotel_name, snapshot_time FROM kf11_room_status_snapshot{hotel_filter}
        UNION ALL
        SELECT hotel_name, snapshot_time FROM rs01_room_revenue_daily{hotel_filter}
    ) t WHERE hotel_name IS NOT NULL AND TRIM(hotel_name) <> ''
    GROUP BY hotel_name
    ORDER BY MAX(snapshot_time) DESC, COUNT(*) DESC, hotel_name
    """
    pms_room_sql = f"""
    SELECT DISTINCT name FROM (
        SELECT room_type_name AS name FROM kf11_room_status_snapshot{hotel_filter}
        UNION
        SELECT room_type_name AS name FROM rs01_room_revenue_daily{hotel_filter}
    ) t WHERE name IS NOT NULL AND TRIM(name) <> '' AND name <> '预订单'
    ORDER BY name
    """
    meituan_room_sql = """
    SELECT DISTINCT room_type_name AS name
    FROM meituan_ota_goods_price_mapping
    WHERE room_type_name IS NOT NULL AND TRIM(room_type_name) <> ''
    ORDER BY room_type_name
    """
    ctrip_room_sql = """
    SELECT DISTINCT room_type_name AS name
    FROM ctrip_ota_goods_price_mapping
    WHERE room_type_name IS NOT NULL AND TRIM(room_type_name) <> ''
    ORDER BY room_type_name
    """
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        return (
            _query_names(cur, pms_hotel_sql, pms_params),
            _query_names(cur, pms_room_sql, pms_params),
            _query_names(cur, meituan_room_sql),
            _query_names(cur, ctrip_room_sql),
        )


def list_groups(settings: dict[str, Any]) -> list[dict[str, Any]]:
    sql = """
    SELECT hotel_id, room_type_id, room_type_name, pms_hotel_name,
           pms_room_type_name, ota_hotel_name, source_platform, source_room_type_name,
           is_active, updated_at
    FROM hotel_room_type_mapping
    WHERE (source_product_id='' AND source_platform IN (%s,%s,%s,%s))
       OR (source_product_id<>'' AND is_active=1
           AND source_platform IN (%s,%s,%s,%s))
    ORDER BY updated_at DESC, id DESC
    """
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                *OTA_BASE_LABELS,
                *PRODUCT_PLATFORMS[MEITUAN_PLATFORM],
                *PRODUCT_PLATFORMS[CTRIP_PLATFORM],
            ),
        )
        rows = list(cur.fetchall())
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["hotel_id"]), str(row["room_type_id"]))
        group = groups.setdefault(
            key,
            {
                "hotel_id": row["hotel_id"],
                "pms_hotel_name": row["pms_hotel_name"],
                "hotel_name": row["ota_hotel_name"],
                "ctrip_hotel_name": "",
                "room_type_id": row["room_type_id"],
                "room_type_name": row["room_type_name"],
                "pms_room_type_name": "",
                "meituan_room_type_name": "",
                "ctrip_room_type_name": "",
                "is_active": 0,
                "updated_at": row["updated_at"],
            },
        )
        source_platform = str(row["source_platform"])
        if row["pms_hotel_name"]:
            group["pms_hotel_name"] = row["pms_hotel_name"]
        if row.get("pms_room_type_name"):
            group["pms_room_type_name"] = row["pms_room_type_name"]
        platform = next(
            (
                logical
                for logical, labels in PRODUCT_PLATFORMS.items()
                if source_platform in labels
            ),
            source_platform,
        )
        if platform == PMS_PLATFORM:
            group["pms_hotel_name"] = row["pms_hotel_name"]
            group["pms_room_type_name"] = row["source_room_type_name"]
        elif platform == MEITUAN_PLATFORM:
            group["hotel_name"] = row["ota_hotel_name"]
            group["meituan_room_type_name"] = row["source_room_type_name"]
        elif platform == CTRIP_PLATFORM:
            group["ctrip_hotel_name"] = row["ota_hotel_name"]
            group["ctrip_room_type_name"] = row["source_room_type_name"]
        group["is_active"] = max(int(group["is_active"]), int(row["is_active"] or 0))
        if row["updated_at"] and row["updated_at"] > group["updated_at"]:
            group["updated_at"] = row["updated_at"]
    return sorted(groups.values(), key=lambda item: item["updated_at"], reverse=True)


def get_group(
    settings: dict[str, Any], hotel_id: str, room_type_id: str
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in list_groups(settings)
            if str(item["hotel_id"]) == hotel_id and str(item["room_type_id"]) == room_type_id
        ),
        None,
    )


def _insert_base(cur, data: dict[str, str], platform: str, source_name: str) -> None:
    ota_hotel_name = (
        data["hotel_name"] if platform == MEITUAN_PLATFORM else data["ctrip_hotel_name"]
    )
    cur.execute(
        """
        INSERT INTO hotel_room_type_mapping (
            hotel_id, pms_hotel_name, room_type_id, room_type_name,
            pms_room_type_name, source_platform, ota_hotel_name,
            source_room_type_name, ota_room_type_name, source_product_id,
            source_product_name, rate_plan_name, product_cipher,
            mapping_status, match_rule, match_confidence, is_active
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'','','','',
                  'CONFIRMED','MANUAL',1.00,1)
        ON DUPLICATE KEY UPDATE
            pms_hotel_name=VALUES(pms_hotel_name),
            room_type_name=VALUES(room_type_name),
            pms_room_type_name=VALUES(pms_room_type_name),
            ota_hotel_name=VALUES(ota_hotel_name),
            ota_room_type_name=VALUES(ota_room_type_name),
            mapping_status='CONFIRMED', match_rule='MANUAL',
            match_confidence=1.00, is_active=1
        """,
        (
            data["hotel_id"],
            data["pms_hotel_name"],
            data["room_type_id"],
            data["room_type_name"],
            data["pms_room_type_name"],
            platform,
            ota_hotel_name,
            source_name,
            source_name,
        ),
    )


def _collect_aliases(
    cur, hotel_id: str, room_type_id: str, *, active_only: bool = True
) -> dict[str, set[str]]:
    aliases = {platform: set() for platform in BASE_PLATFORMS}
    if not hotel_id or not room_type_id:
        return aliases
    active_sql = "AND is_active=1" if active_only else "AND mapping_status<>'REJECTED'"
    cur.execute(
        f"""
        SELECT source_platform, source_room_type_name, pms_room_type_name
        FROM hotel_room_type_mapping
        WHERE hotel_id=%s AND room_type_id=%s {active_sql}
          AND source_room_type_name<>''
        """,
        (hotel_id, room_type_id),
    )
    for row in cur.fetchall():
        if row["pms_room_type_name"]:
            aliases[PMS_PLATFORM].add(str(row["pms_room_type_name"]))
        source_platform = str(row["source_platform"])
        logical_platform = next(
            (
                platform
                for platform, labels in PRODUCT_PLATFORMS.items()
                if source_platform in labels
            ),
            source_platform,
        )
        if logical_platform in aliases:
            aliases[logical_platform].add(str(row["source_room_type_name"]))
    return aliases


def _sync_product_rows(
    cur,
    data: dict[str, str],
    original_hotel_id: str,
    original_id: str,
) -> None:
    old_hotel_id = original_hotel_id or data["hotel_id"]
    platform_fields = {
        MEITUAN_PLATFORM: ("meituan_room_type_name", "hotel_name"),
        CTRIP_PLATFORM: ("ctrip_room_type_name", "ctrip_hotel_name"),
    }
    for platform, (room_field, hotel_field) in platform_fields.items():
        source_name = data[room_field]
        labels = PRODUCT_PLATFORMS[platform]
        if original_id:
            cur.execute(
                """
                UPDATE hotel_room_type_mapping
                SET hotel_id=%s,room_type_id=%s,room_type_name=%s,
                    pms_hotel_name=%s,pms_room_type_name=%s
                WHERE hotel_id=%s AND room_type_id=%s
                  AND source_product_id<>'' AND source_platform IN (%s,%s)
                """,
                (
                    data["hotel_id"],
                    data["room_type_id"],
                    data["room_type_name"],
                    data["pms_hotel_name"],
                    data["pms_room_type_name"],
                    old_hotel_id,
                    original_id,
                    *labels,
                ),
            )
        if not source_name:
            continue
        cur.execute(
            """
            UPDATE hotel_room_type_mapping
            SET hotel_id=%s, pms_hotel_name=%s, room_type_id=%s,
                room_type_name=%s, pms_room_type_name=%s,
                ota_hotel_name=%s, ota_room_type_name=%s,
                mapping_status='CONFIRMED', match_rule='MANUAL',
                match_confidence=1.00, review_note=NULL, is_active=1
            WHERE hotel_id IN (%s,%s) AND source_product_id<>''
              AND source_platform IN (%s,%s)
              AND BINARY source_room_type_name=BINARY %s
            """,
            (
                data["hotel_id"],
                data["pms_hotel_name"],
                data["room_type_id"],
                data["room_type_name"],
                data["pms_room_type_name"],
                data[hotel_field],
                source_name,
                old_hotel_id,
                data["hotel_id"],
                *labels,
                source_name,
            ),
        )
    sync_meituan_products(cur)


def save_group(
    settings: dict[str, Any],
    data: dict[str, str],
    original_hotel_id: str,
    original_id: str,
) -> dict[str, Any]:
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        old_hotel_id = original_hotel_id or data["hotel_id"]
        old_aliases = _collect_aliases(cur, old_hotel_id, original_id)
        cur.execute(
            """
            SELECT source_room_type_name, room_type_id
            FROM hotel_room_type_mapping
            WHERE hotel_id=%s AND is_active=1
              AND ((source_product_id='' AND source_platform IN (%s,%s,%s,%s)
                    AND BINARY pms_room_type_name=BINARY %s)
                OR (source_platform IN (%s,%s)
                    AND BINARY source_room_type_name=BINARY %s)
                OR (source_platform IN (%s,%s)
                    AND BINARY source_room_type_name=BINARY %s))
              AND room_type_id NOT IN (%s,%s) LIMIT 1
            """,
            (
                data["hotel_id"],
                *OTA_BASE_LABELS,
                data["pms_room_type_name"],
                *PRODUCT_PLATFORMS[MEITUAN_PLATFORM],
                data["meituan_room_type_name"],
                *PRODUCT_PLATFORMS[CTRIP_PLATFORM],
                data["ctrip_room_type_name"],
                original_id or data["room_type_id"],
                data["room_type_id"],
            ),
        )
        conflict = cur.fetchone()
        if conflict:
            raise ValueError(
                f"{conflict['source_room_type_name']} 已映射到房型 {conflict['room_type_id']}"
            )
        if original_id:
            cur.execute(
                """
                UPDATE hotel_room_type_mapping
                SET hotel_id=%s,room_type_id=%s,room_type_name=%s,
                    pms_hotel_name=%s,pms_room_type_name=%s
                WHERE hotel_id=%s AND room_type_id=%s AND source_product_id=''
                  AND source_platform IN (%s,%s,%s,%s)
                """,
                (
                    data["hotel_id"],
                    data["room_type_id"],
                    data["room_type_name"],
                    data["pms_hotel_name"],
                    data["pms_room_type_name"],
                    original_hotel_id or data["hotel_id"],
                    original_id,
                    *OTA_BASE_LABELS,
                ),
            )
        _insert_base(cur, data, MEITUAN_PLATFORM, data["meituan_room_type_name"])
        if data["ctrip_room_type_name"]:
            _insert_base(cur, data, CTRIP_PLATFORM, data["ctrip_room_type_name"])
        _sync_product_rows(cur, data, original_hotel_id, original_id)
        conn.commit()
    new_aliases = {
        PMS_PLATFORM: {data["pms_room_type_name"]},
        MEITUAN_PLATFORM: {data["meituan_room_type_name"]},
        CTRIP_PLATFORM: {data["ctrip_room_type_name"]} if data["ctrip_room_type_name"] else set(),
    }
    return {
        "hotel_ids": {old_hotel_id, data["hotel_id"]},
        "room_type_ids": {value for value in (original_id, data["room_type_id"]) if value},
        "aliases": {
            platform: old_aliases[platform] | new_aliases[platform]
            for platform in BASE_PLATFORMS
        },
        "target_hotel_id": data["hotel_id"],
    }


def set_active(
    settings: dict[str, Any], hotel_id: str, room_type_id: str, active: bool
) -> dict[str, Any] | None:
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        aliases = _collect_aliases(cur, hotel_id, room_type_id, active_only=False)
        cur.execute(
            """
            UPDATE hotel_room_type_mapping SET is_active=%s
            WHERE hotel_id=%s AND room_type_id=%s
              AND mapping_status<>'REJECTED'
            """,
            (1 if active else 0, hotel_id, room_type_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return {
        "hotel_ids": {hotel_id},
        "room_type_ids": {room_type_id},
        "aliases": aliases,
        "target_hotel_id": hotel_id,
    }


def error_message(exc: Exception) -> str:
    if isinstance(exc, pymysql.err.IntegrityError):
        return "保存失败：该统一房型映射已存在"
    return f"操作失败：{exc}"
