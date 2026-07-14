# -*- coding: utf-8 -*-

import json
import os
import pandas as pd
import pymysql
from datetime import datetime

# 导入统一配置
from config import HOTEL_CONFIG, DB_CONFIG, OUTPUT_CONFIG

HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()

# =========================================================
# 1️⃣ 时间处理
# =========================================================

def parse_datetime(v):
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
    except:
        return None


def parse_date(v):
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except:
        return None


# =========================================================
# 2️⃣ JSON → RS01结构转换
# =========================================================

def transform_rs01(json_data):

    rows = []

    data_list = json_data["data"]["dataList"]

    # 使用配置中的酒店名称
    hotel_name = HOTEL_CONFIG["name"]
    snapshot_time = datetime.now()

    for item in data_list:

        rows.append({
            "hotel_name": hotel_name,
            "hotel_id": HOTEL_ID,
            "source_platform": HOTEL_CONFIG["source_platform"],

            "business_date": parse_date(item.get("businessDate")),

            "room_no": item.get("roomNumber"),
            "room_type_name": item.get("roomType"),
            "guest_name": item.get("customerName"),

            "customer_source": item.get("customerCategory"),

            "checkin_time": parse_datetime(item.get("checkinTime")),
            "checkout_time": parse_datetime(item.get("departureTime")),

            "rack_rate": item.get("marketPrice"),
            "price_type": item.get("roomRateType"),
            "room_daily_price": item.get("actualPrice"),

            "stay_type": item.get("checkinType"),
            "charge_subject": item.get("roomPriceCategory") or "",

            "room_nights": item.get("roomNights"),
            "room_fee": item.get("roomFee"),

            "operator_name": item.get("operator"),
            "order_id": item.get("orderNumber") or "",

            "snapshot_time": snapshot_time
        })

    return rows


# =========================================================
# 3️⃣ 打印验证（很重要）
# =========================================================

def print_table(rows):

    print("\n========== RS01 转换结果 ==========\n")

    for r in rows[:20]:
        print(
            r["room_no"],
            r["room_type_name"],
            r["guest_name"],
            r["room_daily_price"],
            r["room_fee"]
        )

    print("\n总条数:", len(rows))


# =========================================================
# 4️⃣ 保存到 Excel
# =========================================================

def save_to_excel(rows, output_dir=OUTPUT_CONFIG["base_dir"]):
    """保存数据到Excel"""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_path = os.path.join(output_dir, f"rs01_room_fee_{timestamp}.xlsx")
    df = pd.DataFrame(rows)
    df.to_excel(excel_path, index=False)
    print(f"\n📥 Excel 文件已保存: {excel_path}")
    return excel_path


# =========================================================
# 5️⃣ 写入 MySQL
# =========================================================

def insert_mysql(rows, conn=None):
    """使用统一配置文件中的数据库连接"""
    owns_connection = conn is None
    conn = conn or pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    insert_sql = """
    INSERT INTO rs01_room_revenue_daily (
        hotel_name,
        hotel_id,
        source_platform,
        business_date,
        room_no,
        room_type_name,
        guest_name,
        customer_source,
        checkin_time,
        checkout_time,
        rack_rate,
        price_type,
        room_daily_price,
        stay_type,
        charge_subject,
        room_nights,
        room_fee,
        operator_name,
        order_id,
        snapshot_time
    ) VALUES (
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
    )
    ON DUPLICATE KEY UPDATE
        hotel_name = VALUES(hotel_name),
        source_platform = VALUES(source_platform),
        room_type_name = VALUES(room_type_name),
        guest_name = VALUES(guest_name),
        customer_source = VALUES(customer_source),
        checkin_time = VALUES(checkin_time),
        checkout_time = VALUES(checkout_time),
        rack_rate = VALUES(rack_rate),
        price_type = VALUES(price_type),
        room_daily_price = VALUES(room_daily_price),
        stay_type = VALUES(stay_type),
        room_nights = VALUES(room_nights),
        room_fee = VALUES(room_fee),
        operator_name = VALUES(operator_name),
        snapshot_time = VALUES(snapshot_time),
        updated_at = CURRENT_TIMESTAMP
    """

    inserted_count = 0
    updated_count = 0

    for r in rows:
        cursor.execute(insert_sql, (
            r["hotel_name"],
            r["hotel_id"],
            r["source_platform"],
            r["business_date"],
            r["room_no"],
            r["room_type_name"],
            r["guest_name"],
            r["customer_source"],
            r["checkin_time"],
            r["checkout_time"],
            r["rack_rate"],
            r["price_type"],
            r["room_daily_price"],
            r["stay_type"],
            r["charge_subject"],
            r["room_nights"],
            r["room_fee"],
            r["operator_name"],
            r["order_id"],
            r["snapshot_time"]
        ))
        if cursor.rowcount == 1:
            inserted_count += 1
        else:
            updated_count += 1

    conn.commit()
    cursor.close()
    if owns_connection:
        conn.close()
    
    print(f"\n📊 写入结果: 新增 {inserted_count} 条, 更新 {updated_count} 条")


# =========================================================
# 5️⃣ 主入口
# =========================================================

def main(conn=None):

    json_path = os.path.join(OUTPUT_CONFIG["json_dir"], "RS01.json")

    with open(json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)

    rows = transform_rs01(json_data)

    print_table(rows)

    # 暂不生成中间结果Excel
    # save_to_excel(rows)

    insert_mysql(rows, conn)

    print("\n✔ RS01入库完成")


if __name__ == "__main__":
    main()
