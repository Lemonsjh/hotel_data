# -*- coding: utf-8 -*-
"""
JD01 预订数据 ETL 脚本
功能：
1. 读取 PMS JD01 JSON
2. 转换为 jd01_bookings 表结构
3. 可选：写入 MySQL

作者：AI生成
用途：酒店PMS数据标准化入库
"""

import json
import os
from datetime import datetime, date
import pymysql
import pandas as pd

# 导入统一配置
from config import HOTEL_CONFIG, DB_CONFIG, OUTPUT_CONFIG

HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()

# =========================================================
# 1️⃣ 时间解析工具函数
# =========================================================

def parse_datetime(value):
    """
    将字符串时间转为 datetime
    示例：2026-06-22 15:00:00
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except:
            return None


def parse_date(value):
    """
    从 datetime 字符串提取 date
    示例：2026-06-22 15:00:00 → 2026-06-22
    """
    if not value:
        return None
    dt = parse_datetime(value)
    return dt.date() if dt else None


# =========================================================
# 2️⃣ JSON → 数据库结构转换核心函数
# =========================================================

def transform_jd01(json_data, hotel_name=None):
    # 使用配置中的酒店名称
    if hotel_name is None:
        hotel_name = HOTEL_CONFIG["name"]
    """
    将 JD01 JSON 转换为 jd01_bookings 可入库结构
    简化时间字段：去掉重复的日期/时间，用 datetime 代替
    """

    result = []

    # 遍历订单列表
    for item in json_data["data"]["dataList"]:

        # ---------------------------------------------
        # 单条订单映射（JSON → DB字段）
        # ---------------------------------------------
        record = {
            # 基础信息
            "hotel_name": hotel_name,
            "hotel_id": HOTEL_ID,
            "order_id": item.get("orderNo"),  # 订单号

            # 下单时间
            "booking_time": parse_datetime(item.get("orderDate")),

            # 客户信息
            "contact": item.get("contractName"),
            "guest_source": item.get("source"),          # 客源（中介/非会员）
            "member_level": item.get("customerLevel"),    # 会员等级/渠道

            # 入住信息（合并为 datetime，不再分开日期和时间）
            "arrival_time": parse_datetime(item.get("estArriveTime")),
            "departure_time": parse_datetime(item.get("estDepatureTime")),

            # 房间信息
            "room_type_name": item.get("roomType"),
            "room_count": item.get("roomCount", 1),

            # 价格信息
            "price_type": item.get("roomPriceType"),
            "room_price": item.get("roomPrice"),

            # 付款信息
            "prepayment": item.get("prePayAmount") or 0.00,
            "guarantee_method": item.get("prePaymentType"),

            # 订单状态
            "booking_status": item.get("orderStatus"),

            # 保留时间（超时释放）
            "hold_time": parse_datetime(item.get("expireKeepTime")),

            # 备注 & 操作
            "remarks": item.get("remark"),
            "operator_name": item.get("operator"),

            # 数据快照时间（系统生成）
            "snapshot_time": datetime.now()
        }

        # 加入结果列表
        result.append(record)

    return result


# =========================================================
# 3️⃣ MySQL 写入函数（可选）
# =========================================================

def insert_to_mysql(data_list, conn=None):
    """
    将转换后的数据写入 MySQL（去重）
    使用统一配置文件中的数据库连接
    """

    owns_connection = conn is None
    conn = conn or pymysql.connect(**DB_CONFIG)

    cursor = conn.cursor()

    # 检查记录是否存在的SQL（简化时间字段）
    check_sql = """
    SELECT COUNT(*) FROM jd01_booking_detail
    WHERE hotel_name = %(hotel_name)s AND hotel_id = %(hotel_id)s AND order_id = %(order_id)s
      AND booking_time = %(booking_time)s AND contact = %(contact)s
      AND guest_source = %(guest_source)s AND member_level = %(member_level)s
      AND arrival_time = %(arrival_time)s AND departure_time = %(departure_time)s
      AND room_type_name = %(room_type_name)s AND room_count = %(room_count)s
      AND price_type = %(price_type)s AND room_price = %(room_price)s
      AND prepayment = %(prepayment)s AND guarantee_method = %(guarantee_method)s
      AND booking_status = %(booking_status)s AND hold_time = %(hold_time)s
      AND remarks = %(remarks)s AND operator_name = %(operator_name)s
    """

    # SQL插入语句（简化时间字段：合并日期时间）
    insert_sql = """
    INSERT INTO jd01_booking_detail (
        hotel_name,
        hotel_id,
        order_id,
        booking_time,
        contact,
        guest_source,
        member_level,
        arrival_time,
        departure_time,
        room_type_name,
        room_count,
        price_type,
        room_price,
        prepayment,
        guarantee_method,
        booking_status,
        hold_time,
        remarks,
        operator_name,
        snapshot_time
    )
    VALUES (
        %(hotel_name)s,
        %(hotel_id)s,
        %(order_id)s,
        %(booking_time)s,
        %(contact)s,
        %(guest_source)s,
        %(member_level)s,
        %(arrival_time)s,
        %(departure_time)s,
        %(room_type_name)s,
        %(room_count)s,
        %(price_type)s,
        %(room_price)s,
        %(prepayment)s,
        %(guarantee_method)s,
        %(booking_status)s,
        %(hold_time)s,
        %(remarks)s,
        %(operator_name)s,
        %(snapshot_time)s
    )
    """

    inserted_count = 0
    skipped_count = 0

    # 批量写入（去重）
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
# 4️⃣ 主函数入口
# =========================================================

def main(conn=None):
    """
    主流程：
    1. 读取 JSON
    2. 转换结构
    3. 输出检查
    4. 写入数据库
    """

    # ---------------------------------------------
    # 读取 JSON 文件
    # ---------------------------------------------
    json_path = os.path.join(OUTPUT_CONFIG["json_dir"], "JD01.json")

    with open(json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)

    # ---------------------------------------------
    # 数据转换
    # ---------------------------------------------
    data_list = transform_jd01(json_data)

    # ---------------------------------------------
    # 输出检查（防止错数据）
    # ---------------------------------------------
    print("\n====== 转换完成 ======")
    print("总条数:", len(data_list))
    print("示例数据:")
    print(data_list[0])

    # ---------------------------------------------
    # 写入数据库（取消注释即可启用）
    # ---------------------------------------------
    insert_to_mysql(data_list, conn)

    # ---------------------------------------------
    # 保存为 Excel（新增）
    # ---------------------------------------------
    # 暂不生成中间结果Excel
    # save_to_excel(data_list)

    # ---------------------------------------------
    # 打印表格（新增）
    # ---------------------------------------------
    print_table(data_list)


# =========================================================
# 5️⃣ 程序入口
# =========================================================
# =========================================================
# 4️⃣ 🆕 结果查看函数（新增）
# =========================================================

def save_to_excel(data_list, output_dir=OUTPUT_CONFIG["base_dir"]):
    """
    将转换结果保存为 Excel 文件
    """
    import os
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 生成带时间戳的文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_path = os.path.join(output_dir, f"jd01_bookings_{timestamp}.xlsx")
    
    # 转换为 DataFrame
    df = pd.DataFrame(data_list)
    
    # 保存为 Excel
    df.to_excel(excel_path, index=False)
    
    print(f"\n📥 Excel 文件已保存: {excel_path}")
    return excel_path


def print_table(data_list):
    """
    将转换结果以表格形式打印出来（用于验证ETL是否正确）
    """

    print("\n" + "=" * 120)
    print("📊 JD01 转换结果预览")
    print("=" * 120)

    # 表头（简化时间显示）
    print("{:<15} {:<8} {:<6} {:<10} {:<19} {:<19} {:<20} {:<8} {:<6}".format(
        "order_id", "contact", "source", "member", "arrive", "depart", "room_type_name", "room_price", "status"
    ))

    print("-" * 120)

    # 每一行数据
    for row in data_list:
        # 格式化 datetime 显示
        arrive_str = row["arrival_time"].strftime("%Y-%m-%d %H:%M") if row["arrival_time"] else ""
        depart_str = row["departure_time"].strftime("%Y-%m-%d %H:%M") if row["departure_time"] else ""
        
        print("{:<15} {:<8} {:<6} {:<10} {:<19} {:<19} {:<20} {:<8} {:<6}".format(
            row["order_id"][:15] if row["order_id"] else "",
            row["contact"] or "",
            row["guest_source"] or "",
            row["member_level"] or "",
            arrive_str,
            depart_str,
            (row["room_type_name"] or "")[:18],
            str(row["room_price"]),
            row["booking_status"]
        ))

    print("=" * 120)
    print(f"总条数: {len(data_list)}")
    print("=" * 120)


if __name__ == "__main__":
    main()
