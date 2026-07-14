from __future__ import annotations

from typing import Any


MEITUAN_LABELS = ("美团", "meituan")


def sync_meituan_products(cur: Any) -> dict[str, int]:
    """Refresh current Meituan product rows from the latest goods snapshot."""
    labels = MEITUAN_LABELS
    cur.execute(
        """
        UPDATE hotel_room_type_mapping m
        JOIN meituan_ota_goods_price_mapping g
          ON BINARY g.hotel_id=BINARY m.hotel_id
         AND BINARY CAST(g.ota_product_id AS CHAR)=BINARY m.source_product_id
        LEFT JOIN (
            SELECT hotel_id,room_type_id,
                   MAX(NULLIF(pms_hotel_name,'')) AS pms_hotel_name,
                   MAX(NULLIF(pms_room_type_name,'')) AS pms_room_type_name
            FROM hotel_room_type_mapping
            WHERE source_product_id='' AND is_active=1
            GROUP BY hotel_id,room_type_id
        ) p
          ON BINARY p.hotel_id=BINARY m.hotel_id
         AND BINARY p.room_type_id=BINARY m.room_type_id
        SET m.pms_hotel_name=COALESCE(NULLIF(p.pms_hotel_name,''),m.pms_hotel_name),
            m.pms_room_type_name=COALESCE(NULLIF(p.pms_room_type_name,''),m.pms_room_type_name),
            m.source_room_type_name=g.room_type_name,
            m.ota_room_type_name=g.room_type_name,
            m.source_product_name=COALESCE(g.ota_product_name,''),
            m.rate_plan_name=COALESCE(g.rate_plan_name,''),
            m.is_hour_room=IF(COALESCE(g.ota_product_name,'') REGEXP '-[0-9]+([.][0-9]+)?小时-',1,0),
            m.last_seen_at=COALESCE(g.snapshot_time,m.last_seen_at),
            m.is_active=1
        WHERE m.source_platform IN (%s,%s) AND m.source_product_id<>''
        """,
        labels,
    )
    updated = cur.rowcount

    cur.execute(
        """
        INSERT INTO hotel_room_type_mapping (
            hotel_id,pms_hotel_name,room_type_id,room_type_name,pms_room_type_name,
            source_platform,ota_hotel_name,source_room_type_name,ota_room_type_name,
            source_product_id,source_product_name,rate_plan_name,
            mapping_status,match_rule,match_confidence,is_hour_room,is_active,last_seen_at
        )
        SELECT g.hotel_id,
               b.pms_hotel_name,
               b.room_type_id,b.room_type_name,
               b.pms_room_type_name,
               %s,b.ota_hotel_name,g.room_type_name,g.room_type_name,
               CAST(g.ota_product_id AS CHAR),COALESCE(g.ota_product_name,''),
               COALESCE(g.rate_plan_name,''),'AUTO','ROOM_ID',1.00,
               IF(COALESCE(g.ota_product_name,'') REGEXP '-[0-9]+([.][0-9]+)?小时-',1,0),
               1,COALESCE(g.snapshot_time,CURRENT_TIMESTAMP)
        FROM meituan_ota_goods_price_mapping g
        JOIN hotel_room_type_mapping b
          ON BINARY b.hotel_id=BINARY g.hotel_id
         AND b.source_platform IN (%s,%s)
         AND b.source_product_id='' AND b.is_active=1
         AND BINARY b.source_room_type_name=BINARY g.room_type_name
        WHERE g.ota_product_id IS NOT NULL AND g.ota_product_id<>''
        ON DUPLICATE KEY UPDATE
            pms_hotel_name=VALUES(pms_hotel_name),
            pms_room_type_name=VALUES(pms_room_type_name),
            source_product_name=VALUES(source_product_name),
            rate_plan_name=VALUES(rate_plan_name),
            is_hour_room=VALUES(is_hour_room),
            last_seen_at=VALUES(last_seen_at),
            is_active=1
        """,
        (labels[0], *labels),
    )
    inserted = cur.rowcount

    cur.execute(
        """
        UPDATE hotel_room_type_mapping m
        LEFT JOIN meituan_ota_goods_price_mapping g
          ON BINARY g.hotel_id=BINARY m.hotel_id
         AND BINARY CAST(g.ota_product_id AS CHAR)=BINARY m.source_product_id
        SET m.is_active=0
        WHERE m.source_platform IN (%s,%s) AND m.source_product_id<>''
          AND m.hotel_id IN (
              SELECT hotel_id FROM meituan_ota_goods_price_mapping
              WHERE hotel_id IS NOT NULL AND hotel_id<>''
          )
          AND g.id IS NULL
        """,
        labels,
    )
    return {"updated": updated, "inserted_or_refreshed": inserted, "deactivated": cur.rowcount}
