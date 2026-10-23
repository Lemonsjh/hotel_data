# -*- coding: utf-8 -*-
"""
JD04 续住数据 ETL（稳定版）
"""

import json
from datetime import datetime
import pymysql
import pandas as pd
import os

# 导入统一配置
from config import HOTEL_CONFIG, DB_CONFIG, OUTPUT_CONFIG

HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()

# =========================================================
# 1️⃣ 工具函数
# =========================================================

def safe_str(v):
    return "" if v is None else str(v)

def safe_float(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0

def parse_datetime(v):
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        try:
            return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return None


# =========================================================
# 2️⃣ JSON → JD04表结构
# =========================================================

def transform_jd04(json_data, hotel_name=None):
    # 使用配置中的酒店名称
    if hotel_name is None:
        hotel_name = HOTEL_CONFIG["name"]

    result = []
    payload = json_data.get("data") or {}
    if not isinstance(payload, dict):
        print("⚠️ JD04 返回空数据，按 0 条处理")
        return result

    for item in payload.get("dataList") or []:

        record = {
            # 基础
            "hotel_name": hotel_name,
            "hotel_id": HOTEL_ID,
            "order_id": safe_str(item.get("checkinNo")),

            # 渠道/来源
            "channel_source": safe_str(item.get("channel")),
            "guest_source": safe_str(item.get("customerCategory")),
            "checkin_type": safe_str(item.get("checkinType")),

            # 客人/房间
            "guest_name": safe_str(item.get("customerName")),
            "room_type_name": safe_str(item.get("roomType")),
            "room_no": safe_str(item.get("roomNumber")),

            # 价格
            "price_type": safe_str(item.get("roomPriceType")),
            "room_price": safe_float(item.get("roomPrice")),

            # 时间
            "checkin_time": parse_datetime(item.get("arriveTime")),
            "original_checkout_time": parse_datetime(item.get("oldDepartureTime")),
            "checkout_time": parse_datetime(item.get("departureTime")),

            # 操作信息
            "op_type": safe_str(item.get("operationType")),
            "operator_name": safe_str(item.get("operator")),
            "op_time": parse_datetime(item.get("operatorTime")),

            # 状态
            "status": safe_str(item.get("status")),

            # 系统时间
            "snapshot_time": parse_datetime(
                (payload.get("variables") or {}).get("currentTime")
            ) or datetime.now()
        }

        result.append(record)

    return result


# =========================================================
# 3️⃣ 保存为 Excel
# =========================================================

def save_to_excel(data_list, output_dir=OUTPUT_CONFIG["base_dir"]):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_path = os.path.join(output_dir, f"jd04_extensions_{timestamp}.xlsx")
    df = pd.DataFrame(data_list)
    df.to_excel(excel_path, index=False)
    print(f"\n📥 Excel 文件已保存: {excel_path}")
    return excel_path


# =========================================================
# 4️⃣ 打印检查（必须有）
# =========================================================

def print_table(data_list):
    print("\n========= JD04 转换结果 =========\n")

    for r in data_list[:10]:
        print(
            r["order_id"],
            r["guest_name"],
            r["channel_source"],
            r["room_no"],
            r["checkout_time"],
            r["op_type"],
            r["room_price"]
        )

    print("\n总条数:", len(data_list))


# =========================================================
# 4️⃣ MySQL写入（可选）
# =========================================================

def insert_to_mysql(data_list, conn=None):
    """使用统一配置文件中的数据库连接"""
    owns_connection = conn is None
    conn = conn or pymysql.connect(**DB_CONFIG)

    cursor = conn.cursor()

    # 检查记录是否存在的SQL
    check_sql = """
    SELECT COUNT(*) FROM jd04_inhouse_extension
    WHERE hotel_name = %(hotel_name)s AND hotel_id = %(hotel_id)s AND order_id = %(order_id)s
      AND channel_source = %(channel_source)s AND guest_source = %(guest_source)s
      AND checkin_type = %(checkin_type)s AND guest_name = %(guest_name)s
      AND room_type_name = %(room_type_name)s AND room_no = %(room_no)s
      AND price_type = %(price_type)s AND room_price = %(room_price)s
      AND checkin_time = %(checkin_time)s AND original_checkout_time = %(original_checkout_time)s
      AND checkout_time = %(checkout_time)s AND op_type = %(op_type)s
      AND operator_name = %(operator_name)s AND op_time = %(op_time)s
      AND status = %(status)s
    """

    # 插入SQL
    insert_sql = """
    INSERT INTO jd04_inhouse_extension (
        hotel_name, hotel_id, order_id, channel_source, guest_source,
        checkin_type, guest_name, room_type_name, room_no,
        price_type, room_price,
        checkin_time, original_checkout_time, checkout_time,
        op_type, operator_name, op_time,
        status, snapshot_time
    )
    VALUES (
        %(hotel_name)s, %(hotel_id)s, %(order_id)s, %(channel_source)s, %(guest_source)s,
        %(checkin_type)s, %(guest_name)s, %(room_type_name)s, %(room_no)s,
        %(price_type)s, %(room_price)s,
        %(checkin_time)s, %(original_checkout_time)s, %(checkout_time)s,
        %(op_type)s, %(operator_name)s, %(op_time)s,
        %(status)s, %(snapshot_time)s
    )
    """

    inserted_count = 0
    skipped_count = 0

    for row in data_list:
        # 检查记录是否已存在
        cursor.execute(check_sql, row)
        count = cursor.fetchone()[0]
        
        if count == 0:
            # 记录不存在，插入
            cursor.execute(insert_sql, row)
            inserted_count += 1
        else:
            # 记录已存在，跳过
            skipped_count += 1

    conn.commit()
    cursor.close()
    if owns_connection:
        conn.close()
    
    print(f"\n📊 插入结果: 新增 {inserted_count} 条, 跳过 {skipped_count} 条重复记录")


# =========================================================
# 5️⃣ 主函数
# =========================================================

def main(conn=None):

    path = os.path.join(OUTPUT_CONFIG["json_dir"], "JD04.json")

    with open(path, "r", encoding="utf-8") as f:
        json_data = json.load(f)

    data_list = transform_jd04(json_data)

    # 暂不生成中间结果Excel
    # save_to_excel(data_list)
    print_table(data_list)


    # 写入 MySQL 数据
    # ---------------------------------------------
    insert_to_mysql(data_list, conn)


if __name__ == "__main__":
    main()
