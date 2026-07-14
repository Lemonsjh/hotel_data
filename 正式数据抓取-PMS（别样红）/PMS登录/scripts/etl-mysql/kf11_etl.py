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
# 1️⃣ 安全工具函数
# =========================================================

def safe_str(v):
    return "" if v is None else str(v)


def parse_datetime(v):
    if not v:
        return None
    try:
        # 尝试解析带毫秒的格式
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S.%f")
    except:
        try:
            # 尝试解析不带毫秒的格式
            return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        except:
            return None


# =========================================================
# 2️⃣ KF11 JSON → 行结构
# =========================================================

def transform_kf11(json_data):

    rows = []

    data_list = json_data["data"]["data"]["dataList"]

    # 使用配置中的酒店名称
    hotel_name = HOTEL_CONFIG["name"]
    snapshot_time = datetime.now()
    business_date = snapshot_time.date()

    for item in data_list:

        rows.append({
            "hotel_name": hotel_name,
            "hotel_id": HOTEL_ID,

            "business_date": business_date,

            "room_no": safe_str(item.get("roomNumber")),
            "room_type_name": safe_str(item.get("roomTypeName")),
            "room_status": safe_str(item.get("roomStatusName")),

            "guest_name": safe_str(item.get("mainCustomerName")),

            "checkin_time": parse_datetime(item.get("arriveTime")),
            "checkout_time": parse_datetime(item.get("depatureTime")),

            "snapshot_time": snapshot_time
        })

    return rows


# =========================================================
# 3️⃣ 打印检查（非常重要）
# =========================================================

def print_table(rows):

    print("\n========= KF11 房态转换结果 =========\n")

    for r in rows[:20]:
        print(
            r["room_no"],
            r["room_type_name"],
            r["room_status"],
            r["guest_name"]
        )

    print("\n总条数:", len(rows))


# =========================================================
# 3️⃣ 保存到 Excel
# =========================================================

def save_to_excel(rows, output_dir=OUTPUT_CONFIG["base_dir"]):
    """保存数据到Excel"""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_path = os.path.join(output_dir, f"kf11_room_status_{timestamp}.xlsx")
    df = pd.DataFrame(rows)
    df.to_excel(excel_path, index=False)
    print(f"\n📥 Excel 文件已保存: {excel_path}")
    return excel_path


# =========================================================
# 4️⃣ 写入 MySQL
# =========================================================

def insert_mysql(rows, conn=None):
    """同一酒店、日期、房号只保留最新房态。"""
    owns_connection = conn is None
    conn = conn or pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    upsert_sql = """
    INSERT INTO kf11_room_status_snapshot (
        hotel_name,
        hotel_id,
        business_date,
        room_no,
        room_type_name,
        room_status,
        guest_name,
        checkin_time,
        checkout_time,
        snapshot_time
    ) VALUES (
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
    )
    ON DUPLICATE KEY UPDATE
        hotel_name = VALUES(hotel_name),
        room_type_name = VALUES(room_type_name),
        room_status = VALUES(room_status),
        guest_name = VALUES(guest_name),
        checkin_time = VALUES(checkin_time),
        checkout_time = VALUES(checkout_time),
        snapshot_time = VALUES(snapshot_time)
    """

    inserted_count = 0
    updated_count = 0

    for r in rows:
        cursor.execute(upsert_sql, (
            r["hotel_name"],
            r["hotel_id"],
            r["business_date"],
            r["room_no"],
            r["room_type_name"],
            r["room_status"],
            r["guest_name"],
            r["checkin_time"],
            r["checkout_time"],
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

    json_path = os.path.join(OUTPUT_CONFIG["json_dir"], "KF11.json")

    with open(json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)

    rows = transform_kf11(json_data)

    print_table(rows)

    # 暂不生成中间结果Excel
    # save_to_excel(rows)

    # 写入数据库
    insert_mysql(rows, conn)

    print("\n✔ KF11入库完成")


if __name__ == "__main__":
    main()
