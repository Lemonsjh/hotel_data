from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pymysql

from mapping_product_sync import sync_ctrip_products, sync_meituan_products
import price_tasks
import runner


PMS_BYH_PLATFORM = "pms_byh"
PMS_BYPMS_PLATFORM = "pms_bypms"
PMS_FOXHIS_PLATFORM = "pms_foxhis"
PMS_MEITUAN_PLATFORM = "pms_meituan"
PMS_HUIYUN_PLATFORM = "pms_huiyun"
PMS_REZEN_PLATFORM = "pms_rezen"
PMS_YOLO_PLATFORM = "pms_yolo"
PMS_PLATFORM = PMS_BYH_PLATFORM
BYPMS_SOURCE_LABEL = "PMS（宝寓）"
FOXHIS_SOURCE_LABEL = "PMS（Foxhis）"
YOLO_SOURCE_LABEL = "PMS（余乐通）"
MEITUAN_PLATFORM = "meituan"
CTRIP_PLATFORM = "ctrip"
PMS_PLATFORMS = (PMS_BYH_PLATFORM, PMS_BYPMS_PLATFORM, PMS_FOXHIS_PLATFORM, PMS_MEITUAN_PLATFORM, PMS_HUIYUN_PLATFORM, PMS_REZEN_PLATFORM, PMS_YOLO_PLATFORM)
BASE_PLATFORMS = (*PMS_PLATFORMS, MEITUAN_PLATFORM, CTRIP_PLATFORM)
PRODUCT_PLATFORMS = {
    MEITUAN_PLATFORM: ("美团", "meituan"),
    CTRIP_PLATFORM: ("携程", "ctrip"),
}
OTA_BASE_LABELS = (*PRODUCT_PLATFORMS[MEITUAN_PLATFORM], *PRODUCT_PLATFORMS[CTRIP_PLATFORM])
PMS_ALIASES_FIELD = "pms_room_type_names"
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
REQUIRED_FIELDS = ("hotel_id", "pms_hotel_name", "room_type_id", "pms_room_type_name")
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


def active_pms_platform(settings: dict[str, Any]) -> str:
    provider = str((settings.get("pms") or {}).get("provider") or "byh").strip().lower()
    return {"bypms": PMS_BYPMS_PLATFORM, "foxhis": PMS_FOXHIS_PLATFORM,
            "meituan_pms": PMS_MEITUAN_PLATFORM, "meituan_rezen": PMS_MEITUAN_PLATFORM,
            "rezen": PMS_REZEN_PLATFORM, "huiyun": PMS_HUIYUN_PLATFORM,
            "yolo": PMS_YOLO_PLATFORM}.get(provider, PMS_BYH_PLATFORM)


def pms_alias_platforms(settings: dict[str, Any]) -> tuple[str, ...]:
    """双系统的原始房型分属两个来源。其它模式仍只有当前这一个平台。"""
    provider = str((settings.get("pms") or {}).get("provider") or "").strip().lower()
    if provider == "meituan_rezen":
        return (PMS_MEITUAN_PLATFORM, PMS_REZEN_PLATFORM)
    return (active_pms_platform(settings),)


def defaults(settings: dict[str, Any]) -> dict[str, Any]:
    pms_key = {PMS_BYPMS_PLATFORM: "bypms", PMS_FOXHIS_PLATFORM: "foxhis",
               PMS_MEITUAN_PLATFORM: "meituan_pms", PMS_HUIYUN_PLATFORM: "huiyun",
               PMS_REZEN_PLATFORM: "rezen", PMS_YOLO_PLATFORM: "yolo"}.get(active_pms_platform(settings), "pms")
    return {
        "hotel_id": str(settings.get("hotel", {}).get("hotel_id", "")).strip(),
        "pms_hotel_name": str(settings.get(pms_key, {}).get("hotel_name", "")).strip(),
        "hotel_name": str(settings.get("meituan", {}).get("hotel_name", "")).strip(),
        "ctrip_hotel_name": str(settings.get("ctrip", {}).get("hotel_name", "")).strip(),
        "pms_room_type_name": "",
        PMS_ALIASES_FIELD: [],
    }


def pms_room_type_names(data: dict[str, Any]) -> list[str]:
    raw = data.get(PMS_ALIASES_FIELD)
    if isinstance(raw, (list, tuple, set)):
        values = raw
    elif raw:
        values = [raw]
    else:
        values = [data.get("pms_room_type_name", "")]

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _merge_pms_names(existing: set[str], requested: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*sorted(existing), *requested]:
        name = str(value or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def validate(data: dict[str, Any]) -> str | None:
    pms_names = pms_room_type_names(data)
    missing = []
    for name in REQUIRED_FIELDS:
        present = bool(pms_names) if name == "pms_room_type_name" else bool(data.get(name))
        if not present:
            missing.append(LABELS[name])
    if missing:
        return "请填写：" + "、".join(missing)
    if not data.get("meituan_room_type_name") and not data.get("ctrip_room_type_name"):
        return "请至少填写美团或携程其中一个房型"
    for platform, room_field, hotel_field in (
        ("美团", "meituan_room_type_name", "hotel_name"),
        ("携程", "ctrip_room_type_name", "ctrip_hotel_name"),
    ):
        if data.get(room_field) and not data.get(hotel_field):
            return f"填写{platform}房型时，也需要填写{platform}酒店名称"
    return None


def _query_names(cur, sql: str, params: tuple[Any, ...] = ()) -> list[str]:
    try:
        cur.execute(sql, params)
        return [str(row["name"]).strip() for row in cur.fetchall() if str(row["name"]).strip()]
    except pymysql.MySQLError:
        return []


def forecast_source_label(settings: dict[str, Any]) -> str | None:
    """下拉选项读取实际写入房态的来源。双系统的房型在合并来源里，不在单独的美团来源里。"""
    provider = str((settings.get("pms") or {}).get("provider") or "").strip().lower()
    if provider == "meituan_rezen":
        return "PMS（美团+Rezen）"
    return {PMS_BYPMS_PLATFORM: BYPMS_SOURCE_LABEL, PMS_FOXHIS_PLATFORM: FOXHIS_SOURCE_LABEL,
            PMS_MEITUAN_PLATFORM: "PMS（美团）", PMS_HUIYUN_PLATFORM: "PMS（慧云）",
            PMS_REZEN_PLATFORM: "PMS（Rezen）",
            PMS_YOLO_PLATFORM: YOLO_SOURCE_LABEL}.get(active_pms_platform(settings))


def room_options(
    settings: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str]]:
    hotel_id = str((settings.get("hotel") or {}).get("hotel_id") or "").strip()
    source_label = forecast_source_label(settings)
    hotel_filter = " WHERE hotel_id=%s" if hotel_id else ""
    pms_params = (hotel_id, hotel_id) if hotel_id else ()
    forecast_filter = "WHERE hotel_id=%s AND" if hotel_id else "WHERE"
    forecast_params = (hotel_id,) if hotel_id else ()
    byh_hotel_sql = f"""
    SELECT hotel_name AS name FROM (
        SELECT hotel_name, snapshot_time FROM kf11_room_status_snapshot{hotel_filter}
        UNION ALL
        SELECT hotel_name, snapshot_time FROM rs01_room_revenue_daily{hotel_filter}
    ) t WHERE hotel_name IS NOT NULL AND TRIM(hotel_name) <> ''
    GROUP BY hotel_name
    ORDER BY MAX(snapshot_time) DESC, COUNT(*) DESC, hotel_name
    """
    byh_room_sql = f"""
    SELECT DISTINCT room_type_name AS name
    FROM pms_room_type_forecast
    {forecast_filter} room_type_name IS NOT NULL AND TRIM(room_type_name) <> ''
    ORDER BY room_type_name
    """
    bypms_filter = "WHERE source_platform=%s AND hotel_id=%s" if hotel_id else "WHERE source_platform=%s"
    bypms_hotel_sql = f"""
    SELECT hotel_name AS name FROM pms_room_type_forecast {bypms_filter}
    GROUP BY hotel_name ORDER BY MAX(snapshot_time) DESC, hotel_name
    """
    bypms_room_sql = f"""
    SELECT DISTINCT room_type_name AS name FROM pms_room_type_forecast
    {bypms_filter} ORDER BY room_type_name
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
    WHERE hotel_id=%s AND platform_scope='ctrip'
      AND room_type_name IS NOT NULL AND TRIM(room_type_name) <> ''
    ORDER BY room_type_name
    """
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        pms_hotel_sql, pms_room_sql = (bypms_hotel_sql, bypms_room_sql) if source_label else (byh_hotel_sql, byh_room_sql)
        pms_params = (
            (source_label, hotel_id)
            if source_label and hotel_id
            else ((source_label,) if source_label else pms_params)
        )
        forecast_params = pms_params if source_label else forecast_params
        hotels = _query_names(cur, pms_hotel_sql, pms_params)
        rooms = _query_names(cur, pms_room_sql, forecast_params)
        if source_label == "PMS（美团+Rezen）":
            rooms = [name for name in rooms if " / " not in name]
        return (
            hotels,
            rooms,
            _query_names(cur, meituan_room_sql),
            _query_names(cur, ctrip_room_sql, (runner.internal_hotel_id(settings, "ctrip"),)),
        )


def _dual_state_path() -> Path:
    root = Path(os.environ.get("HOTEL_OTA_PROJECT_ROOT") or Path(__file__).resolve().parents[1])
    return root / "OTA采集服务" / "state" / "pms_dual.json"


def _observed_room_types() -> tuple[list[str], list[str]]:
    try:
        data = json.loads(_dual_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], []
    bucket = data.get("room_types") if isinstance(data, dict) else None
    if not isinstance(bucket, dict):
        return [], []

    def names(side: str) -> list[str]:
        raw = bucket.get(side)
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    return names("meituan"), names("rezen")


def side_room_options(settings: dict[str, Any]) -> tuple[list[str], list[str]] | None:
    """双系统下拉使用合并前的原始房型。合并后的「名称 / 名称」不能当作一个别名。"""
    if pms_alias_platforms(settings) != (PMS_MEITUAN_PLATFORM, PMS_REZEN_PLATFORM):
        return None
    meituan, rezen = (set(names) for names in _observed_room_types())
    known_meituan, known_rezen = set(meituan), set(rezen)
    hotel_id = str((settings.get("hotel") or {}).get("hotel_id") or "").strip()
    hotel_sql = " AND hotel_id=%s" if hotel_id else ""
    hotel_params = (hotel_id,) if hotel_id else ()
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT source_platform, source_room_type_name AS name FROM hotel_room_type_mapping "
                "WHERE source_product_id='' AND mapping_status<>'REJECTED' AND source_room_type_name<>'' "
                "AND source_platform IN (%s,%s)" + hotel_sql,
                (PMS_MEITUAN_PLATFORM, PMS_REZEN_PLATFORM, *hotel_params),
            )
            for row in cur.fetchall():
                platform, name = str(row["source_platform"]), str(row["name"]).strip()
                if not name:
                    continue
                if platform == PMS_MEITUAN_PLATFORM:
                    meituan.add(name)
                elif platform == PMS_REZEN_PLATFORM:
                    rezen.add(name)
        except pymysql.MySQLError:
            pass
        try:
            cur.execute(
                "SELECT DISTINCT room_type_name AS name, pms_room_type_id AS raw_id "
                "FROM pms_room_type_forecast WHERE source_platform=%s "
                "AND room_type_name IS NOT NULL AND TRIM(room_type_name)<>''" + hotel_sql,
                ("PMS（美团+Rezen）", *hotel_params),
            )
            forecast_rows = list(cur.fetchall())
        except pymysql.MySQLError:
            forecast_rows = []
    for row in forecast_rows:
        name = str(row["name"]).strip()
        raw_id = str(row.get("raw_id") or "")
        if not name or " / " in name or name in known_meituan or name in known_rezen:
            continue
        if raw_id.startswith("mt:"):
            meituan.add(name)
        elif raw_id.startswith("rz:"):
            rezen.add(name)
        else:
            meituan.add(name)
            rezen.add(name)
    return sorted(meituan), sorted(rezen)


def list_groups(settings: dict[str, Any]) -> list[dict[str, Any]]:
    pms_placeholders = ",".join(["%s"] * len(PMS_PLATFORMS))
    sql = f"""
    SELECT hotel_id, room_type_id, room_type_name, pms_hotel_name,
           pms_room_type_name, ota_hotel_name, source_platform, source_room_type_name,
           is_active, updated_at
    FROM hotel_room_type_mapping
    WHERE mapping_status<>'REJECTED'
      AND ((source_product_id='' AND source_platform IN ({pms_placeholders},%s,%s,%s,%s))
       OR (source_product_id<>'' AND is_active=1
           AND source_platform IN (%s,%s,%s,%s)))
    ORDER BY updated_at DESC, id DESC
    """
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                *PMS_PLATFORMS,
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
                PMS_ALIASES_FIELD: set(),
                "meituan_pms_room_type_names": set(),
                "rezen_pms_room_type_names": set(),
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
            group[PMS_ALIASES_FIELD].add(str(row["pms_room_type_name"]))

        platform = next(
            (
                logical
                for logical, labels in PRODUCT_PLATFORMS.items()
                if source_platform in labels
            ),
            source_platform,
        )
        if platform in PMS_PLATFORMS:
            group["pms_hotel_name"] = row["pms_hotel_name"]
            if row["source_room_type_name"]:
                source_name = str(row["source_room_type_name"])
                group[PMS_ALIASES_FIELD].add(source_name)
                if platform == PMS_MEITUAN_PLATFORM:
                    group["meituan_pms_room_type_names"].add(source_name)
                elif platform == PMS_REZEN_PLATFORM:
                    group["rezen_pms_room_type_names"].add(source_name)
        elif platform == MEITUAN_PLATFORM:
            group["hotel_name"] = row["ota_hotel_name"]
            group["meituan_room_type_name"] = row["source_room_type_name"]
        elif platform == CTRIP_PLATFORM:
            group["ctrip_hotel_name"] = row["ota_hotel_name"]
            group["ctrip_room_type_name"] = row["source_room_type_name"]

        group["is_active"] = max(int(group["is_active"]), int(row["is_active"] or 0))
        if row["updated_at"] and row["updated_at"] > group["updated_at"]:
            group["updated_at"] = row["updated_at"]

    result = []
    for group in groups.values():
        aliases = sorted(group[PMS_ALIASES_FIELD])
        group[PMS_ALIASES_FIELD] = aliases
        group["meituan_pms_room_type_names"] = sorted(group["meituan_pms_room_type_names"])
        group["rezen_pms_room_type_names"] = sorted(group["rezen_pms_room_type_names"])
        group["pms_room_type_name"] = aliases[0] if aliases else ""
        result.append(group)
    return sorted(result, key=lambda item: item["updated_at"], reverse=True)


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


def list_pms_mapping_rows(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one management row per PMS source mapping, not per unified ID."""
    groups = {
        (str(item["hotel_id"]), str(item["room_type_id"])): item
        for item in list_groups(settings)
    }
    platforms = pms_alias_platforms(settings)
    placeholders = ",".join(["%s"] * len(platforms))
    labels = {PMS_MEITUAN_PLATFORM: "美团 PMS", PMS_REZEN_PLATFORM: "Rezen"}
    sql = f"""
    SELECT hotel_id, room_type_id, source_platform, source_room_type_name, is_active, updated_at
    FROM hotel_room_type_mapping
    WHERE source_platform IN ({placeholders}) AND source_product_id=''
      AND source_room_type_name<>'' AND mapping_status<>'REJECTED'
    ORDER BY updated_at DESC, id DESC
    """
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        cur.execute(sql, platforms)
        aliases = cur.fetchall()

    rows = []
    for alias in aliases:
        group = groups.get((str(alias["hotel_id"]), str(alias["room_type_id"])))
        if not group:
            continue
        row = dict(group)
        row["source_platform"] = alias["source_platform"]
        row["pms_room_type_name"] = alias["source_room_type_name"]
        row["pms_side_label"] = labels.get(str(alias["source_platform"]), "") if len(platforms) > 1 else ""
        row["is_active"] = int(alias["is_active"] or 0)
        row["updated_at"] = alias["updated_at"]
        rows.append(row)
    return rows


def _insert_base(cur, data: dict[str, Any], platform: str, source_name: str) -> None:
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


def _insert_pms_alias(cur, data: dict[str, Any], source_name: str, pms_platform: str) -> None:
    cur.execute(
        """
        INSERT INTO hotel_room_type_mapping (
            hotel_id, pms_hotel_name, room_type_id, room_type_name,
            pms_room_type_name, source_platform, ota_hotel_name,
            source_room_type_name, ota_room_type_name, source_product_id,
            source_product_name, rate_plan_name, product_cipher,
            mapping_status, match_rule, match_confidence, is_active
        ) VALUES (%s,%s,%s,%s,%s,%s,'',%s,'','','','','',
                  'CONFIRMED','MANUAL',1.00,1)
        ON DUPLICATE KEY UPDATE
            pms_hotel_name=VALUES(pms_hotel_name),
            room_type_name=VALUES(room_type_name),
            pms_room_type_name=VALUES(pms_room_type_name),
            mapping_status='CONFIRMED', match_rule='MANUAL',
            match_confidence=1.00, review_note=NULL, is_active=1
        """,
        (
            data["hotel_id"],
            data["pms_hotel_name"],
            data["room_type_id"],
            data["room_type_name"],
            source_name,
            pms_platform,
            source_name,
        ),
    )


def _replace_ota_base_rows(
    cur,
    data: dict[str, Any],
    original_hotel_id: str,
    original_id: str,
) -> None:
    """Keep one editable OTA room-type selection per platform and unified ID."""
    old_hotel_id = original_hotel_id or data["hotel_id"]
    platform_fields = (
        (MEITUAN_PLATFORM, "meituan_room_type_name"),
        (CTRIP_PLATFORM, "ctrip_room_type_name"),
    )
    for platform, room_field in platform_fields:
        source_name = str(data[room_field] or "").strip()
        labels = PRODUCT_PLATFORMS[platform]
        if original_id:
            cur.execute(
                """
                UPDATE hotel_room_type_mapping
                SET mapping_status='REJECTED', is_active=0
                WHERE hotel_id=%s AND room_type_id=%s
                  AND source_product_id='' AND source_platform IN (%s,%s)
                  AND (%s='' OR BINARY source_room_type_name<>BINARY %s)
                """,
                (old_hotel_id, original_id, *labels, source_name, source_name),
            )
        if source_name:
            _insert_base(cur, data, platform, source_name)


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
        source_platform = str(row["source_platform"])
        if source_platform in PMS_PLATFORMS and row["source_room_type_name"]:
            aliases[source_platform].add(str(row["source_room_type_name"]))
        elif row["pms_room_type_name"]:
            aliases[PMS_PLATFORM].add(str(row["pms_room_type_name"]))
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


def _assert_no_conflicts(
    cur,
    data: dict[str, Any],
    original_id: str,
    pms_names: list[str],
    pms_platform: str,
) -> None:
    old_id = original_id or str(data["room_type_id"])
    new_id = str(data["room_type_id"])

    for source_name in pms_names:
        cur.execute(
            """
            SELECT source_room_type_name, room_type_id
            FROM hotel_room_type_mapping
            WHERE hotel_id=%s AND is_active=1 AND mapping_status<>'REJECTED'
              AND (
                    (source_platform=%s AND source_product_id=''
                     AND BINARY source_room_type_name=BINARY %s)
                 OR BINARY pms_room_type_name=BINARY %s
              )
              AND room_type_id NOT IN (%s,%s)
            LIMIT 1
            """,
            (
                data["hotel_id"],
                pms_platform,
                source_name,
                source_name,
                old_id,
                new_id,
            ),
        )
        conflict = cur.fetchone()
        if conflict:
            raise ValueError(f"{source_name} 已映射到房型 {conflict['room_type_id']}")

    for platform, room_field in (
        (MEITUAN_PLATFORM, "meituan_room_type_name"),
        (CTRIP_PLATFORM, "ctrip_room_type_name"),
    ):
        source_name = str(data.get(room_field) or "").strip()
        if not source_name:
            continue
        labels = PRODUCT_PLATFORMS[platform]
        cur.execute(
            """
            SELECT source_room_type_name, room_type_id
            FROM hotel_room_type_mapping
            WHERE hotel_id=%s AND is_active=1 AND mapping_status<>'REJECTED'
              AND source_platform IN (%s,%s)
              AND BINARY source_room_type_name=BINARY %s
              AND room_type_id NOT IN (%s,%s)
            LIMIT 1
            """,
            (data["hotel_id"], *labels, source_name, old_id, new_id),
        )
        conflict = cur.fetchone()
        if conflict:
            raise ValueError(f"{source_name} 已映射到房型 {conflict['room_type_id']}")


def _sync_pms_alias_rows(
    cur,
    data: dict[str, Any],
    original_hotel_id: str,
    original_id: str,
    pms_names: list[str],
    pms_platform: str,
) -> None:
    old_hotel_id = original_hotel_id or str(data["hotel_id"])
    if original_id:
        cur.execute(
            """
            UPDATE hotel_room_type_mapping
            SET hotel_id=%s,room_type_id=%s,room_type_name=%s,
                pms_hotel_name=%s,pms_room_type_name=source_room_type_name
            WHERE hotel_id=%s AND room_type_id=%s
              AND source_platform=%s AND source_product_id=''
              AND mapping_status<>'REJECTED'
            """,
            (
                data["hotel_id"],
                data["room_type_id"],
                data["room_type_name"],
                data["pms_hotel_name"],
                old_hotel_id,
                original_id,
                pms_platform,
            ),
        )

    for source_name in pms_names:
        _insert_pms_alias(cur, data, source_name, pms_platform)

    if not pms_names:
        cur.execute(
            """
            UPDATE hotel_room_type_mapping
            SET mapping_status='REJECTED', is_active=0
            WHERE hotel_id=%s AND room_type_id=%s
              AND source_platform=%s AND source_product_id=''
            """,
            (data["hotel_id"], data["room_type_id"], pms_platform),
        )
        return

    placeholders = ",".join(["%s"] * len(pms_names))
    cur.execute(
        f"""
        UPDATE hotel_room_type_mapping
        SET mapping_status='REJECTED', is_active=0
        WHERE hotel_id=%s AND room_type_id=%s
          AND source_platform=%s AND source_product_id=''
          AND source_room_type_name NOT IN ({placeholders})
        """,
        (
            data["hotel_id"],
            data["room_type_id"],
            pms_platform,
            *pms_names,
        ),
    )


def _sync_product_rows(
    cur,
    data: dict[str, Any],
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
    sync_ctrip_products(cur)


def _save_dual_aliases(
    settings: dict[str, Any],
    data: dict[str, Any],
    original_hotel_id: str,
    original_id: str,
) -> dict[str, Any]:
    provider = str((settings.get("pms") or {}).get("provider") or "").strip().lower()
    if provider != "meituan_rezen":
        raise ValueError("只有美团 PMS + Rezen 可以分侧保存房型别名")
    side_names = {
        PMS_MEITUAN_PLATFORM: pms_room_type_names({PMS_ALIASES_FIELD: data.get("meituan_pms_room_type_names")}),
        PMS_REZEN_PLATFORM: pms_room_type_names({PMS_ALIASES_FIELD: data.get("rezen_pms_room_type_names")}),
    }
    joined = [name for names in side_names.values() for name in names if " / " in name]
    if joined:
        raise ValueError("请分别选择两侧的原始房型，不要使用合并后的名称")
    if not any(side_names.values()):
        raise ValueError("至少选择一个 PMS 房型")

    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        old_hotel_id = original_hotel_id or data["hotel_id"]
        lookup_id = original_id or data["room_type_id"]
        old_aliases = _collect_aliases(cur, old_hotel_id, lookup_id, active_only=False)
        if not original_id:
            for platform in (PMS_MEITUAN_PLATFORM, PMS_REZEN_PLATFORM):
                side_names[platform] = [
                    name for name in _merge_pms_names(old_aliases[platform], side_names[platform])
                    if " / " not in name
                ]
        if not any(side_names.values()):
            raise ValueError("至少选择一个 PMS 房型")
        data["pms_room_type_name"] = (side_names[PMS_MEITUAN_PLATFORM] or side_names[PMS_REZEN_PLATFORM])[0]
        data[PMS_ALIASES_FIELD] = side_names[PMS_MEITUAN_PLATFORM] + [
            name for name in side_names[PMS_REZEN_PLATFORM] if name not in side_names[PMS_MEITUAN_PLATFORM]
        ]
        for platform, names in side_names.items():
            _assert_no_conflicts(cur, data, original_id, names, platform)

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

        for platform, names in side_names.items():
            _sync_pms_alias_rows(cur, data, original_hotel_id, original_id, names, platform)
        _replace_ota_base_rows(cur, data, original_hotel_id, original_id)
        _sync_product_rows(cur, data, original_hotel_id, original_id)
        conn.commit()

    new_aliases = {platform: set() for platform in BASE_PLATFORMS}
    new_aliases[PMS_MEITUAN_PLATFORM] = set(side_names[PMS_MEITUAN_PLATFORM])
    new_aliases[PMS_REZEN_PLATFORM] = set(side_names[PMS_REZEN_PLATFORM])
    new_aliases[MEITUAN_PLATFORM] = {data["meituan_room_type_name"]} if data.get("meituan_room_type_name") else set()
    new_aliases[CTRIP_PLATFORM] = {data["ctrip_room_type_name"]} if data.get("ctrip_room_type_name") else set()
    return {
        "hotel_ids": {old_hotel_id, data["hotel_id"]},
        "room_type_ids": {value for value in (original_id, data["room_type_id"]) if value},
        "aliases": {
            platform: old_aliases[platform] | new_aliases[platform]
            for platform in BASE_PLATFORMS
        },
        "target_hotel_id": data["hotel_id"],
    }


def save_group(
    settings: dict[str, Any],
    data: dict[str, Any],
    original_hotel_id: str,
    original_id: str,
) -> dict[str, Any]:
    data = dict(data)
    if data.get("dual_pms_aliases"):
        return _save_dual_aliases(settings, data, original_hotel_id, original_id)
    requested_pms_names = pms_room_type_names(data)
    if not requested_pms_names:
        raise ValueError("至少选择一个 PMS 房型")

    pms_platform = active_pms_platform(settings)
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        old_hotel_id = original_hotel_id or data["hotel_id"]
        lookup_id = original_id or data["room_type_id"]
        old_aliases = _collect_aliases(
            cur, old_hotel_id, lookup_id, active_only=False
        )
        pms_names = requested_pms_names
        if not original_id:
            pms_names = _merge_pms_names(
                old_aliases[pms_platform], requested_pms_names
            )
        data[PMS_ALIASES_FIELD] = pms_names
        data["pms_room_type_name"] = pms_names[0]
        _assert_no_conflicts(cur, data, original_id, pms_names, pms_platform)

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

        _sync_pms_alias_rows(cur, data, original_hotel_id, original_id, pms_names, pms_platform)
        _replace_ota_base_rows(cur, data, original_hotel_id, original_id)
        _sync_product_rows(cur, data, original_hotel_id, original_id)
        conn.commit()

    new_aliases = {
        **{platform: set() for platform in PMS_PLATFORMS},
        pms_platform: set(pms_names),
        MEITUAN_PLATFORM: {data["meituan_room_type_name"]},
        CTRIP_PLATFORM: (
            {data["ctrip_room_type_name"]} if data["ctrip_room_type_name"] else set()
        ),
    }
    return {
        "hotel_ids": {old_hotel_id, data["hotel_id"]},
        "room_type_ids": {
            value for value in (original_id, data["room_type_id"]) if value
        },
        "aliases": {
            platform: old_aliases[platform] | new_aliases[platform]
            for platform in BASE_PLATFORMS
        },
        "target_hotel_id": data["hotel_id"],
    }


def set_pms_alias_active(
    settings: dict[str, Any], hotel_id: str, room_type_id: str, pms_room_type_name: str, active: bool,
    source_platform: str = "",
) -> dict[str, Any] | None:
    allowed = pms_alias_platforms(settings)
    pms_platform = source_platform.strip() or allowed[0]
    if pms_platform not in allowed:
        raise ValueError("房型来源与当前 PMS 不一致")
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE hotel_room_type_mapping SET is_active=%s
            WHERE hotel_id=%s AND room_type_id=%s
              AND source_platform=%s AND source_product_id=''
              AND BINARY source_room_type_name=BINARY %s
              AND mapping_status<>'REJECTED'
            """,
            (1 if active else 0, hotel_id, room_type_id, pms_platform, pms_room_type_name),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return {
        "hotel_ids": {hotel_id},
        "room_type_ids": {room_type_id},
        "aliases": {
            **{platform: set() for platform in PMS_PLATFORMS},
            pms_platform: {pms_room_type_name},
            MEITUAN_PLATFORM: set(),
            CTRIP_PLATFORM: set(),
        },
        "target_hotel_id": hotel_id,
    }


def error_message(exc: Exception) -> str:
    if isinstance(exc, pymysql.err.IntegrityError):
        return "保存失败：该统一房型映射已存在"
    return f"操作失败：{exc}"
