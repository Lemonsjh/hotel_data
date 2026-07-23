#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PMS（别样红）主脚本 - 自动判断登录状态并抓取所有报表
支持强制重新登录: python3 fetch_main.py --login
"""

import sys
import os
import argparse
import pymysql
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

# 添加当前目录和 scripts 文件夹到 Python 路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))
sys.path.insert(0, current_dir)
sys.path.insert(0, os.path.join(current_dir, "scripts"))
ota_service_dir = next(path for path in project_root.iterdir() if (path / "runner.py").is_file())
sys.path.insert(0, str(ota_service_dir))

import pms_utils
import runner
from data_retention import cleanup_pms_history
from mysql_connection import connect_mysql

OUTPUT_DIR = os.path.join(current_dir, "output")


def run_fetch(code, label, fetcher):
    """执行采集，并确认对应 JSON 确实在本轮被更新。"""
    path = os.path.join(OUTPUT_DIR, f"{code}.json")
    before = os.stat(path).st_mtime_ns if os.path.exists(path) else None
    print(f"📥 正在抓取 {code} {label}...")
    try:
        fetcher()
    except Exception as exc:
        print(f"❌ {code} 采集异常: {exc}")
        return False
    after = os.stat(path).st_mtime_ns if os.path.exists(path) else None
    if after is None or after == before:
        print(f"❌ {code} 本轮未生成新数据，禁止使用旧文件入库")
        return False
    return True


def need_login(hours=12, force=False, username=""):
    """判断是否需要重新登录"""
    if force:
        print("🔄 强制重新登录")
        if pms_utils.delete_session():
            print("已删除旧会话文件")
        return True

    info = pms_utils.read_session(require_cookies=True, quiet=True)
    if not info:
        print("🔄 会话不存在或无效，需要登录")
        return True
    if username and info.get("account_fingerprint") != pms_utils.account_fingerprint(username):
        print("🔄 PMS 配置账号已变化，需要重新登录")
        return True
    try:
        login_time = datetime.strptime(info["login_time"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        print("🔄 会话登录时间无效，需要重新登录")
        return True
    elapsed = datetime.now() - login_time
    
    if elapsed > timedelta(hours=hours):
        print(f"🔄 会话已过期（{elapsed.total_seconds()/3600:.1f} 小时前登录），需要重新登录")
        return True
    
    print(f"✅ 会话有效（{elapsed.total_seconds()/60:.1f} 分钟前登录）")
    return False


def main() -> int:
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='PMS报表数据抓取')
    parser.add_argument('--login', action='store_true', help='强制重新登录')
    parser.add_argument('--jy03-backfill', action='store_true', help='JY03 补采当前年和去年全部月份')
    parser.add_argument('--jl01-start-date', help='JL01 补采开始营业日，格式 YYYY-MM-DD')
    parser.add_argument('--jl01-end-date', help='JL01 补采结束营业日，未填则与开始日期相同')
    parser.add_argument('--jl02-start-date', help='JL02 补采开始营业日，格式 YYYY-MM-DD')
    parser.add_argument('--jl02-end-date', help='JL02 补采结束营业日，未填则与开始日期相同')
    parser.add_argument('--reports', nargs='+', choices=['RS01', 'JD01', 'JD04', 'JY01', 'JY03', 'JL01', 'JL02', 'JL11', 'KF11', 'FORECAST', 'ROOM_STATUS'], help='仅执行指定报表，例如 --reports JL01')
    args = parser.parse_args()
    
    print("=" * 60)
    print("PMS（别样红）报表数据抓取主脚本")
    print("=" * 60)
    
    # 判断是否需要登录
    username = os.environ.get("PMS_USERNAME", "").strip()
    password = os.environ.get("PMS_PASSWORD", "").strip()
    if need_login(hours=12, force=args.login, username=username):
        print("\n🔐 正在登录...")
        if not username or not password:
            print("❌ PMS_USERNAME 或 PMS_PASSWORD 未配置")
            return 1
        from login import login
        success = login(username, password)
        if not success:
            print("❌ 登录失败，无法继续")
            return 1

    hotel_name = pms_utils.ensure_hotel_name()
    if not hotel_name:
        print("❌ 无法从 PMS 获取酒店名称，请检查 pms.hotel_name 配置")
        return 1
    os.environ["PMS_HOTEL_NAME"] = hotel_name
    
    # 抓取所有报表（调用修复版脚本）
    print("\n📊 开始抓取报表数据...")
    


    # from rs01_test import rs01_test
    # from jd01_test import jd01_test
    # from jd04_test import jd04_test
    # from jy01_test import jy01_test
    # from jy03_test import jy03_test
    # from kf11_test import kf11_test
    from fetch_kf11 import fetch_kf11
    from fetch_jy01 import fetch_jy01
    from fetch_jy03 import fetch_jy03
    from fetch_jl01 import fetch_jl01
    from fetch_jl02 import fetch_jl02
    from fetch_jl11 import fetch_jl11
    from fetch_room_type_forecast import fetch_room_type_forecast
    from fetch_room_type_hourly_status import fetch_room_type_hourly_status
    from fetch_rs01 import fetch_rs01
    from fetch_jd01 import fetch_jd01
    from fetch_jd04 import fetch_jd04
    
    fetch_jobs = [
        ("RS01", "房费日结", fetch_rs01),
        ("JD01", "预订", fetch_jd01),
        ("JD04", "续住", fetch_jd04),
        ("JY01", "经营日报", fetch_jy01),
        ("JY03", "经营月报", lambda: fetch_jy03(args.jy03_backfill)),
        ("JL01", "经理综合日报", lambda: fetch_jl01(args.jl01_start_date, args.jl01_end_date)),
        ("JL02", "经营业绩日报", lambda: fetch_jl02(args.jl02_start_date, args.jl02_end_date)),
        ("JL11", "房型分类统计", fetch_jl11),
        ("KF11", "房态快照", fetch_kf11),
        ("FORECAST", "房类预测", fetch_room_type_forecast),
        ("ROOM_STATUS", "每小时房态", fetch_room_type_hourly_status),
    ]
    if args.reports:
        selected = set(args.reports)
        fetch_jobs = [job for job in fetch_jobs if job[0] in selected]
    fetch_results = {code: run_fetch(code, label, fetcher) for code, label, fetcher in fetch_jobs}
    
    print("\n🔄 开始执行 ETL 转换...")
    
    # 添加 etl-mysql 路径
    sys.path.insert(0, os.path.join(current_dir, "scripts/etl-mysql"))
    
    from rs01_etl import main as rs01_etl_main
    from jd01_etl import main as jd01_etl_main
    from jd04_etl import main as jd04_etl_main
    from jy01_etl import main as jy01_etl_main
    from jy03_etl import main as jy03_etl_main
    from jl01_etl import main as jl01_etl_main
    from jl02_etl import main as jl02_etl_main
    from jl11_etl import main as jl11_etl_main
    from room_type_forecast_etl import main as room_type_forecast_etl_main
    from room_type_hourly_status_etl import main as room_type_hourly_status_etl_main
    from kf11_etl import main as kf11_etl_main
    from config import DB_CONFIG
    
    etl_jobs = [
        ("RS01", rs01_etl_main),
        ("JD01", jd01_etl_main),
        ("JD04", jd04_etl_main),
        ("JY01", jy01_etl_main),
        ("JY03", jy03_etl_main),
        ("JL01", jl01_etl_main),
        ("JL02", jl02_etl_main),
        ("JL11", jl11_etl_main),
        ("KF11", kf11_etl_main),
        ("FORECAST", room_type_forecast_etl_main),
        ("ROOM_STATUS", room_type_hourly_status_etl_main),
    ]
    etl_jobs = [job for job in etl_jobs if job[0] in fetch_results]
    db_conn = connect_mysql(DB_CONFIG)
    try:
        for code, etl in etl_jobs:
            if not fetch_results[code]:
                print(f"⏭️ 跳过 {code} ETL，避免旧数据重复入库")
                continue
            print(f"🔄 正在转换 {code} 数据...")
            try:
                try:
                    db_conn.ping()
                except pymysql.MySQLError:
                    db_conn.close()
                    db_conn = connect_mysql(DB_CONFIG)
                etl(db_conn)
            except Exception:
                db_conn.rollback()
                raise
        try:
            cleanup_pms_history(db_conn, runner.load_settings())
        except Exception as exc:
            print(f"Warning: PMS retention cleanup failed: {exc}")
    finally:
        db_conn.close()

    failed = [code for code, success in fetch_results.items() if not success]
    if failed:
        print(f"\n❌ PMS 部分采集失败: {', '.join(failed)}")
        return 2
    print("\n🎉 所有报表抓取和ETL转换完成！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
