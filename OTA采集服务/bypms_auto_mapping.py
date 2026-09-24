"""Build only missing ByPMS/Meituan/Ctrip mappings from a verified channel snapshot."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import pymysql


CHANNELS = {"Meituan": "meituan", "Ctrip": "ctrip"}
OTA_LABELS = {"meituan": ("meituan", "美团"), "ctrip": ("ctrip", "携程")}
HOUR_ROOM = re.compile(r"-[0-9]+(?:\.[0-9]+)?小时-")


def _logical_platform(value: str) -> str:
    return next((key for key, labels in OTA_LABELS.items() if value in labels), value)


def load_inputs(cur, hotel_id: str) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    cur.execute(
        """SELECT channel,channel_unit_id,channel_unit_name,relation_id,
                  bypms_room_type_name,relation_count
           FROM bypms_channel_unit_mapping_snapshot
           WHERE hotel_id=%s AND channel IN ('Meituan','Ctrip')""",
        (hotel_id,),
    )
    snapshot = list(cur.fetchall())
    cur.execute(
        """SELECT room_type_id,room_type_name,pms_room_type_name,source_platform,
                  source_room_type_name,source_product_id,mapping_status,is_active,remark
           FROM hotel_room_type_mapping WHERE hotel_id=%s""",
        (hotel_id,),
    )
    existing = list(cur.fetchall())
    goods: dict[str, list[dict]] = {}
    for platform, table in (("meituan", "meituan_ota_goods_price_mapping"),
                            ("ctrip", "ctrip_ota_goods_price_mapping")):
        cur.execute(
            f"""SELECT ota_room_type_id,room_type_name,ota_product_id,ota_product_name,
                       {"rate_plan_name" if platform == "meituan" else "product_cipher"} AS extra,
                       {"is_hour_room" if platform == "ctrip" else "NULL"} AS is_hour_room,
                       {"price_editable_flag" if platform == "ctrip" else "NULL"} AS price_editable_flag
                FROM `{table}` WHERE hotel_id=%s AND ota_room_type_id IS NOT NULL""",
            (hotel_id,),
        )
        goods[platform] = list(cur.fetchall())
    return snapshot, existing, goods


def build_plan(snapshot: list[dict], existing: list[dict], goods: dict[str, list[dict]],
               master_types: dict[str, str]) -> dict[str, Any]:
    plan: dict[str, Any] = {"aliases": [], "bases": [], "renames": [], "products": [], "conflicts": []}
    by_unit: dict[tuple[str, str], list[dict]] = defaultdict(list)
    ids_by_master_name: dict[str, set[str]] = defaultdict(set)
    for native_id, name in master_types.items():
        ids_by_master_name[name].add(native_id)
    by_goods: dict[str, dict[str, list[dict]]] = {}
    for platform, rows in goods.items():
        grouped: dict[str, list[dict]] = defaultdict(list)
        for item in rows:
            grouped[str(item.get("ota_room_type_id") or "")].append(item)
        by_goods[platform] = grouped
    for row in snapshot:
        if row.get("channel") in CHANNELS:
            by_unit[(row["channel"], str(row.get("channel_unit_id") or ""))].append(row)

    aliases: dict[str, set[str]] = defaultdict(set)
    base_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    markers: dict[str, list[dict]] = defaultdict(list)
    product_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    room_ids: dict[str, set[str]] = defaultdict(set)
    for row in existing:
        room_id = str(row["room_type_id"])
        source = str(row["source_platform"])
        name = str(row.get("source_room_type_name") or "")
        if source == "pms_bypms" and row.get("source_product_id") == "":
            aliases[name].add(room_id)
        if str(row.get("source_product_id") or ""):
            product_ids[(_logical_platform(source), str(row["source_product_id"]))].add(room_id)
        elif _logical_platform(source) in OTA_LABELS:
            base_names[(_logical_platform(source), name)].add(room_id)
        if row.get("remark") and str(row["remark"]).startswith("BYPMS_AUTO:"):
            markers[str(row["remark"])].append(row)
        room_ids[room_id].add(name if source == "pms_bypms" else "")

    candidates: list[tuple[str, str, str, str, str, list[dict], str]] = []
    for (channel, unit_id), records in sorted(by_unit.items()):
        platform = CHANNELS[channel]
        relations = {(str(r.get("relation_id") or ""), str(r.get("bypms_room_type_name") or "").strip())
                     for r in records}
        if not unit_id or len(relations) != 1 or any(r.get("relation_count") != 1 for r in records):
            plan["conflicts"].append((channel, unit_id, "ambiguous_relation"))
            continue
        native_id, pms_name = next(iter(relations))
        if not re.fullmatch(r"[0-9]+", native_id) or not pms_name or master_types.get(native_id) != pms_name:
            plan["conflicts"].append((channel, unit_id, "unverified_pms_room"))
            continue
        if len(ids_by_master_name[pms_name]) > 1:
            plan["conflicts"].append((channel, unit_id, "duplicate_pms_name"))
            continue
        goods_rows = by_goods.get(platform, {}).get(unit_id, [])
        goods_names = {str(g.get("room_type_name") or "").strip() for g in goods_rows}
        goods_names.discard("")
        if len(goods_names) > 1:
            plan["conflicts"].append((channel, unit_id, "ambiguous_ota_room"))
            continue
        ota_name = next(iter(goods_names)) if goods_names else str(records[0].get("channel_unit_name") or "").strip()
        if not ota_name:
            plan["conflicts"].append((channel, unit_id, "missing_ota_room"))
            continue
        candidates.append((channel, unit_id, native_id, pms_name, ota_name, goods_rows, platform))

    for channel, unit_id, native_id, pms_name, ota_name, goods_rows, platform in candidates:
        known_ids = aliases[pms_name]
        if len(known_ids) > 1:
            plan["conflicts"].append((channel, unit_id, "pms_name_conflict"))
            continue
        room_id = next(iter(known_ids)) if known_ids else native_id
        if not known_ids and room_id in room_ids:
            plan["conflicts"].append((channel, unit_id, "generated_id_conflict"))
            continue
        alias_rows = [r for r in existing if r["source_platform"] == "pms_bypms"
                      and r.get("source_product_id") == "" and r.get("source_room_type_name") == pms_name]
        if any(not r.get("is_active") or r.get("mapping_status") == "REJECTED" for r in alias_rows):
            plan["conflicts"].append((channel, unit_id, "disabled_pms_alias"))
            continue
        marker = f"BYPMS_AUTO:{channel}:{unit_id}"
        prior = markers.get(marker, [])
        name_ids = base_names[(platform, ota_name)]
        if len(prior) > 1 or (prior and (not prior[0]["is_active"] or prior[0]["room_type_id"] != room_id)):
            plan["conflicts"].append((channel, unit_id, "existing_unit_conflict"))
            continue
        if name_ids - {room_id} or any(
            product_ids[(platform, str(g.get("ota_product_id") or ""))] - {room_id}
            for g in goods_rows if g.get("ota_product_id")
        ):
            plan["conflicts"].append((channel, unit_id, "ota_name_conflict"))
            continue
        if any(r.get("mapping_status") == "REJECTED" or not r.get("is_active")
               for r in existing if _logical_platform(str(r["source_platform"])) == platform
               and not r.get("source_product_id") and r.get("source_room_type_name") == ota_name):
            plan["conflicts"].append((channel, unit_id, "disabled_ota_mapping"))
            continue
        if prior and prior[0]["source_room_type_name"] != ota_name:
            if prior[0]["mapping_status"] != "AUTO":
                plan["conflicts"].append((channel, unit_id, "manual_ota_mapping"))
                continue
        if room_id not in aliases[pms_name]:
            plan["aliases"].append((room_id, pms_name, native_id))
            aliases[pms_name].add(room_id)
            room_ids[room_id].add(pms_name)
        if prior and prior[0]["source_room_type_name"] != ota_name:
            plan["renames"].append((prior[0]["source_room_type_name"], ota_name, platform, room_id, marker))
            base_names[(platform, prior[0]["source_room_type_name"])].discard(room_id)
            base_names[(platform, ota_name)].add(room_id)
        elif not prior and not name_ids:
            plan["bases"].append((room_id, pms_name, platform, ota_name, marker))
            base_names[(platform, ota_name)].add(room_id)

        seen_products: set[str] = set()
        for good in goods_rows:
            product_id = str(good.get("ota_product_id") or "").strip()
            if not product_id or product_id in seen_products:
                continue
            seen_products.add(product_id)
            mapped = product_ids[(platform, product_id)]
            if mapped - {room_id}:
                plan["conflicts"].append((channel, unit_id, "product_conflict"))
            elif not mapped:
                plan["products"].append((room_id, pms_name, platform, ota_name, product_id, good))
                mapped.add(room_id)
    return plan


def apply_plan(cur, hotel_id: str, pms_hotel_name: str, ota_hotels: dict[str, str], plan: dict) -> None:
    for room_id, name, native_id in plan["aliases"]:
        cur.execute(
            """INSERT INTO hotel_room_type_mapping
               (hotel_id,pms_hotel_name,room_type_id,room_type_name,pms_room_type_name,
                source_platform,source_room_type_name,source_product_id,mapping_status,
                match_rule,match_confidence,remark)
               VALUES (%s,%s,%s,%s,%s,'pms_bypms',%s,'','AUTO','ROOM_ID',1.00,%s)""",
            (hotel_id, pms_hotel_name, room_id, name, name, name, f"BYPMS_AUTO_ROOM:{native_id}"),
        )
    for old_name, new_name, platform, room_id, marker in plan["renames"]:
        cur.execute(
            """UPDATE hotel_room_type_mapping SET source_room_type_name=%s,ota_room_type_name=%s
               WHERE hotel_id=%s AND room_type_id=%s AND source_platform=%s
                 AND source_product_id='' AND BINARY source_room_type_name=BINARY %s
                 AND remark=%s AND mapping_status='AUTO' AND is_active=1""",
            (new_name, new_name, hotel_id, room_id, platform, old_name, marker),
        )
    for room_id, pms_name, platform, ota_name, marker in plan["bases"]:
        cur.execute(
            """INSERT INTO hotel_room_type_mapping
               (hotel_id,pms_hotel_name,room_type_id,room_type_name,pms_room_type_name,
                source_platform,ota_hotel_name,source_room_type_name,ota_room_type_name,
                source_product_id,mapping_status,match_rule,match_confidence,remark)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'','AUTO','ROOM_ID',1.00,%s)""",
            (hotel_id, pms_hotel_name, room_id, pms_name, pms_name, platform,
             ota_hotels.get(platform, ""), ota_name, ota_name, marker),
        )
    for room_id, pms_name, platform, ota_name, product_id, good in plan["products"]:
        product_name = str(good.get("ota_product_name") or "")
        hour_room = bool(HOUR_ROOM.search(product_name)) if platform == "meituan" else bool(good.get("is_hour_room"))
        cur.execute(
            """INSERT INTO hotel_room_type_mapping
               (hotel_id,pms_hotel_name,room_type_id,room_type_name,pms_room_type_name,
                source_platform,ota_hotel_name,source_room_type_name,ota_room_type_name,
                source_product_id,source_product_name,rate_plan_name,product_cipher,
                price_editable_flag,is_hour_room,mapping_status,match_rule,match_confidence,remark)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'AUTO','ROOM_ID',1.00,%s)""",
            (hotel_id, pms_hotel_name, room_id, pms_name, pms_name,
             "美团" if platform == "meituan" else "ctrip",
             ota_hotels.get(platform, ""), ota_name, ota_name,
             product_id, product_name, str(good.get("extra") or "") if platform == "meituan" else "",
             str(good.get("extra") or "")[:255] if platform == "ctrip" else "",
             good.get("price_editable_flag"), int(hour_room), f"BYPMS_AUTO_PRODUCT:{platform}:{product_id}"),
        )


def migrate_legacy_ids(cur, hotel_id: str) -> tuple[int, int]:
    """Replace only IDs previously generated from verified ByPMS room aliases."""
    cur.execute(
        """SELECT DISTINCT room_type_id,remark FROM hotel_room_type_mapping
           WHERE hotel_id=%s AND source_platform='pms_bypms' AND source_product_id=''
             AND room_type_id LIKE 'BYPMS-%%' AND remark LIKE 'BYPMS_AUTO_ROOM:%%'""",
        (hotel_id,),
    )
    legacy = {}
    for row in cur.fetchall():
        old_id = str(row["room_type_id"])
        native_id = str(row["remark"]).removeprefix("BYPMS_AUTO_ROOM:")
        if old_id != f"BYPMS-{native_id}" or not re.fullmatch(r"[0-9]+", native_id):
            raise RuntimeError(f"ByPMS legacy room ID is inconsistent: {old_id}")
        legacy[old_id] = native_id
    if not legacy:
        return 0, 0
    targets = tuple(legacy.values())
    placeholders = ",".join(["%s"] * len(targets))
    cur.execute(
        f"SELECT DISTINCT room_type_id FROM hotel_room_type_mapping "
        f"WHERE hotel_id=%s AND room_type_id IN ({placeholders})",
        (hotel_id, *targets),
    )
    collisions = [row["room_type_id"] for row in cur.fetchall()]
    if collisions:
        raise RuntimeError(f"ByPMS numeric room IDs already exist: {collisions}")
    cur.execute(
        """SELECT c.TABLE_NAME FROM information_schema.COLUMNS c
           JOIN information_schema.TABLES t ON c.TABLE_SCHEMA=t.TABLE_SCHEMA
             AND c.TABLE_NAME=t.TABLE_NAME
           WHERE c.TABLE_SCHEMA=DATABASE() AND c.COLUMN_NAME='room_type_id'
             AND t.TABLE_TYPE='BASE TABLE'
             AND EXISTS (
               SELECT 1 FROM information_schema.COLUMNS h
               WHERE h.TABLE_SCHEMA=c.TABLE_SCHEMA AND h.TABLE_NAME=c.TABLE_NAME
                 AND h.COLUMN_NAME='hotel_id')"""
    )
    tables = [row["TABLE_NAME"] for row in cur.fetchall()]
    old_ids = tuple(legacy)
    placeholders = ",".join(["%s"] * len(old_ids))
    changed = 0
    for table in tables:
        if not re.fullmatch(r"[A-Za-z0-9_]+", table):
            raise RuntimeError("Unexpected room-type table name")
        cur.execute(
            f"UPDATE `{table}` SET room_type_id=SUBSTRING(room_type_id,7) "
            f"WHERE hotel_id=%s AND room_type_id IN ({placeholders})",
            (hotel_id, *old_ids),
        )
        changed += cur.rowcount
    return len(legacy), changed


def synchronize(connection, hotel_id: str, pms_hotel_name: str, ota_hotels: dict[str, str],
                master_types: dict[str, str]) -> dict[str, int]:
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cur:
            migrated_ids, migrated_rows = migrate_legacy_ids(cur, hotel_id)
            snapshot, existing, goods = load_inputs(cur, hotel_id)
            plan = build_plan(snapshot, existing, goods, master_types)
            changes = sum(len(plan[key]) for key in ("aliases", "bases", "renames", "products"))
            if changes:
                apply_plan(cur, hotel_id, pms_hotel_name, ota_hotels, plan)
        if changes or migrated_rows:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {**{key: len(value) for key, value in plan.items()},
            "legacy_ids_migrated": migrated_ids, "legacy_rows_migrated": migrated_rows}
