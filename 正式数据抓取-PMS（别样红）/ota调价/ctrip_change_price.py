#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
携程 eBooking MySQL 调价脚本。

逻辑：
1. 从 MySQL 表 ctrip_price_task 读取 PENDING 任务
2. 用 getRCRoomPriceSetting 查询携程当前价
3. 如果当前价 = 目标价：直接标记 SUCCESS
4. 如果当前价 != 目标价：自动调用 setRCRoomPrice 调价
5. 调价后再次 getRCRoomPriceSetting 回读验证
6. 成功更新 SUCCESS，失败更新 FAILED

表结构最简版：
CREATE TABLE ctrip_price_task (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    hotel_name VARCHAR(100) NOT NULL,
    ota_product_id BIGINT NOT NULL,
    business_date DATE NOT NULL,
    target_sale_price DECIMAL(10,2) NOT NULL,
    execute_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    product_cipher TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

运行：
1. 自动读取所有 PENDING 任务：
python ctrip_change_price.py

2. 只执行指定酒店：
python ctrip_change_price.py \
  --hotel-name "贵阳智町栖筑优品酒店"

3. 不需要输入 YES：
脚本发现价格不一致后，会自动提交携程改价。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import pymysql
from playwright.sync_api import sync_playwright


# =========================
# MySQL 配置
# 优先读环境变量；没有环境变量则保持为空，避免在代码中保存真实连接信息。
# =========================
DB_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", os.environ.get("HOTEL_OTA_MYSQL_HOST", "127.0.0.1")),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
    "user": os.environ.get("MYSQL_USER", os.environ.get("HOTEL_OTA_MYSQL_USER", "")),
    "password": os.environ.get("MYSQL_PASSWORD", os.environ.get("HOTEL_OTA_MYSQL_PASSWORD", "")),
    "database": os.environ.get("MYSQL_DATABASE", os.environ.get("HOTEL_OTA_MYSQL_DATABASE", "")),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}


# =========================
# 携程配置
# =========================
DEFAULT_URL = "https://ebooking.ctrip.com/rateplan/batchPriceSetting?microJump=true"
DEFAULT_COOKIE_FILE = "./ctrip_cookie.txt"
DEFAULT_PROFILE = "./ctrip-user-data"

SERVICE_CODE = "23783"
GET_PRICE_SETTING = "getRCRoomPriceSetting"
SET_ROOM_PRICE = "setRCRoomPrice"

WEEK_DAYS = [
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
]


POST_HELPER_JS = r"""
(() => {
  window.__ctripPostSoa = async function(serviceCode, serviceOperation, payload) {
    const url = `/restapi/soa2/${serviceCode}/${serviceOperation}`;
    const response = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "x-requested-with": "XMLHttpRequest"
      },
      body: JSON.stringify(payload || {})
    });
    const text = await response.text();
    let data = text;
    try { data = JSON.parse(text); } catch (e) {}
    return { status: response.status, url, data };
  };

  window.__ctripBuildBasePayload = function({ productIds, dateRanges, cipher }) {
    const screenWidth = window.screen && window.screen.width ? window.screen.width : 1470;
    const screenHeight = window.screen && window.screen.height ? window.screen.height : 956;

    function findCookie(name) {
      const hit = document.cookie.split(";").map(x => x.trim()).find(x => x.startsWith(name + "="));
      return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
    }

    const clientId =
      (findCookie("_bfa").split(".")[1] || "") ||
      findCookie("GUID") ||
      findCookie("MKT_CKID") ||
      "";

    return {
      reqHead: {
        host: "ebooking.ctrip.com",
        pathName: "/rateplan/batchPriceSetting",
        locale: navigator.language || "en-US",
        release: "",
        client: {
          deviceType: "PC",
          os: navigator.platform && navigator.platform.includes("Mac") ? "Mac" : "PC",
          osVersion: "",
          deviceName: navigator.platform || "",
          clientId: clientId,
          screenWidth: screenWidth,
          screenHeight: screenHeight,
          isIn: {
            ie: false,
            chrome: !!window.chrome,
            chrome49: false,
            wechat: false,
            firefox: navigator.userAgent.includes("Firefox"),
            ios: /iPhone|iPad|iPod/.test(navigator.userAgent),
            android: /Android/.test(navigator.userAgent)
          },
          isModernBrowser: true,
          browser: navigator.userAgent.includes("Chrome") ? "Chrome" : "",
          browserVersion: "",
          platform: "pc",
          technology: "web"
        },
        ubt: {
          pageid: "10650010602",
          pvid: 1,
          sid: 5,
          vid: "",
          fp: "",
          rmsToken: ""
        },
        gps: { coord: "", lat: "", lng: "", cid: 0, cnm: "" },
        protocal: location.protocol
      },
      roomProductIds: productIds.map(String),
      dateRanges: dateRanges,
      weekDays: ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"],
      withPrice: true,
      cipher: cipher || {},
      head: {
        cid: clientId,
        ctok: "",
        cver: "1.0",
        lang: "01",
        sid: "8888",
        syscode: "09",
        auth: "",
        xsid: "",
        extension: []
      }
    };
  };
})();
"""


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def resolve_near_script(path_text: str) -> str:
    path = Path(path_text).expanduser()
    if path.exists():
        return str(path)
    candidate = Path(__file__).resolve().parent / path_text
    return str(candidate) if candidate.exists() else str(path)


def parse_cookie_header(cookie_header: str) -> list[dict[str, Any]]:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    cookies: list[dict[str, Any]] = []
    for name, morsel in cookie.items():
        cookies.append(
            {
                "name": name,
                "value": morsel.value,
                "domain": ".ctrip.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }
        )
    return cookies


def load_cookie_from_file(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("把携程"):
        return ""
    return text


def to_str_date(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def to_float(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def money_equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    return round(float(a), 2) == round(float(b), 2)


# =========================
# MySQL 任务读取 / 状态更新
# =========================
def load_mysql_tasks(hotel_name: str = "", task_id: int | None = None) -> list[dict[str, Any]]:
    """
    读取 MySQL 待执行任务。

    - 如果传入 hotel_name：只读取该酒店的 PENDING 任务
    - 如果不传 hotel_name：自动读取全部酒店的 PENDING 任务
    """
    if task_id is not None:
        sql = """
        SELECT id, hotel_name, ota_product_id, business_date, target_sale_price, product_cipher
        FROM ctrip_price_task
        WHERE id = %s AND execute_status IN ('PENDING', 'EXECUTING')
        """
        params = (task_id,)
    elif hotel_name:
        sql = """
        SELECT
            id,
            hotel_name,
            ota_product_id,
            business_date,
            target_sale_price,
            product_cipher
        FROM ctrip_price_task
        WHERE hotel_name = %s
          AND execute_status = 'PENDING'
        ORDER BY hotel_name, business_date, ota_product_id
        """
        params = (hotel_name,)
    else:
        sql = """
        SELECT
            id,
            hotel_name,
            ota_product_id,
            business_date,
            target_sale_price,
            product_cipher
        FROM ctrip_price_task
        WHERE execute_status = 'PENDING'
        ORDER BY hotel_name, business_date, ota_product_id
        """
        params = ()

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    tasks: list[dict[str, Any]] = []
    for row in rows:
        tasks.append(
            {
                "id": row["id"],
                "hotel_name": row["hotel_name"],
                "productId": str(row["ota_product_id"]),
                "startDate": to_str_date(row["business_date"]),
                "endDate": to_str_date(row["business_date"]),
                "price": to_float(row["target_sale_price"]),
                "product_cipher": str(row["product_cipher"]).strip(),
            }
        )
    return tasks


def update_task_status(task_id: int, status: str, message: str = "") -> None:
    sql = """
    UPDATE ctrip_price_task
    SET execute_status = %s
    WHERE id = %s
    """

    # 如果你的表后面加了 execute_message 字段，可以改成：
    # UPDATE ctrip_price_task
    # SET execute_status = %s, execute_message = %s
    # WHERE id = %s

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (status, task_id))
        conn.commit()
    finally:
        conn.close()


# =========================
# 携程接口
# =========================
def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def find_number(value: Any, keys: tuple[str, ...], default: float | None = None) -> float | None:
    for item in walk(value):
        for key in keys:
            raw = item.get(key)
            if isinstance(raw, (int, float)):
                return float(raw)
            if isinstance(raw, str):
                try:
                    return float(raw)
                except ValueError:
                    pass
    return default


def post_soa(page, service_code: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    return page.evaluate(
        """
        async ({serviceCode, operation, payload}) => {
          return await window.__ctripPostSoa(serviceCode, operation, payload);
        }
        """,
        {"serviceCode": service_code, "operation": operation, "payload": payload},
    )


def build_base_payload(
    page,
    product_ids: list[str],
    start_date: str,
    end_date: str,
    cipher: dict[str, str],
) -> dict[str, Any]:
    return page.evaluate(
        """
        ({productIds, startDate, endDate, cipher}) => {
          return window.__ctripBuildBasePayload({
            productIds,
            dateRanges: [{startDate, endDate}],
            cipher
          });
        }
        """,
        {
            "productIds": product_ids,
            "startDate": start_date,
            "endDate": end_date,
            "cipher": cipher,
        },
    )


def query_price_setting(
    page,
    rows: list[dict[str, Any]],
    cipher_map: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    start_date = rows[0]["startDate"]
    end_date = rows[0]["endDate"]
    product_ids = [row["productId"] for row in rows]

    missing = [pid for pid in product_ids if not cipher_map.get(pid)]
    if missing:
        raise RuntimeError("缺少 product_cipher: " + ", ".join(missing))

    payload = build_base_payload(page, product_ids, start_date, end_date, cipher_map)

    result = post_soa(page, SERVICE_CODE, GET_PRICE_SETTING, payload)
    if result.get("status") != 200:
        raise RuntimeError(f"{GET_PRICE_SETTING} HTTP {result.get('status')}: {pretty(result)}")

    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{GET_PRICE_SETTING} 返回结构异常: {pretty(result)}")

    res_status = data.get("resStatus") or {}
    if res_status.get("rcode") not in (None, 200):
        raise RuntimeError(f"{GET_PRICE_SETTING} 业务失败: {pretty(result)}")

    setting_map = data.get("roomPriceSettingMap")
    if not isinstance(setting_map, dict):
        raise RuntimeError(f"{GET_PRICE_SETTING} 没有 roomPriceSettingMap: {pretty(result)}")

    for pid in product_ids:
        if pid not in setting_map:
            raise RuntimeError(f"{GET_PRICE_SETTING} 没有查到商品 {pid}: {pretty(result)}")

    return payload, data


def get_current_price(setting_data: dict[str, Any], product_id: str) -> float | None:
    setting_map = setting_data["roomPriceSettingMap"]
    info = setting_map[product_id]
    return find_number(info, ("price", "salePrice", "originalPrice"), None)


def build_room_price_infos(rows: list[dict[str, Any]], setting_map: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        pid = row["productId"]
        target_price = float(row["price"])
        info = setting_map[pid]

        commission_rate = find_number(info, ("commissionRate",), 0.12)
        if commission_rate is None:
            commission_rate = 0.12
        if commission_rate > 1:
            commission_rate = commission_rate / 100

        meal_num = find_number(info, ("mealNum",), 0)
        if meal_num is None:
            meal_num = 0

        result.append(
            {
                "roomProductId": pid,
                "startDate": row["startDate"],
                "endDate": row["endDate"],
                "priceChangeMode": "sale_commissionRate",
                "salePrice": target_price,
                "costPrice": round(target_price * (1 - commission_rate), 2),
                "commissionRate": round(commission_rate, 4),
                "currency": "RMB",
                "mealNum": int(meal_num),
                "excludedRelationRoomProductIds": [],
                "weekDays": WEEK_DAYS,
            }
        )
    return result


def build_set_payload(
    query_payload: dict[str, Any],
    rows: list[dict[str, Any]],
    setting_data: dict[str, Any],
) -> dict[str, Any]:
    setting_map = setting_data["roomPriceSettingMap"]
    room_price_infos = build_room_price_infos(rows, setting_map)

    payload = json.loads(json.dumps(query_payload, ensure_ascii=False))
    payload.pop("roomProductIds", None)
    payload.pop("withPrice", None)

    payload["roomPriceInfos"] = room_price_infos
    payload["isFixedCommission"] = False
    payload["dateRanges"] = [{"startDate": rows[0]["startDate"], "endDate": rows[0]["endDate"]}]
    payload["weekDays"] = WEEK_DAYS
    payload["priceChangeMode"] = "priceMode"
    payload["diffWeekendPrice"] = False
    return payload


def submit_set_room_price(page, payload: dict[str, Any]) -> dict[str, Any]:
    result = post_soa(page, SERVICE_CODE, SET_ROOM_PRICE, payload)
    if result.get("status") != 200:
        raise RuntimeError(f"{SET_ROOM_PRICE} HTTP {result.get('status')}: {pretty(result)}")

    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{SET_ROOM_PRICE} 返回结构异常: {pretty(result)}")

    res_status = data.get("resStatus") or {}
    if res_status.get("rcode") != 200:
        raise RuntimeError(f"{SET_ROOM_PRICE} 业务失败: {pretty(result)}")

    return result


def group_tasks_by_date(tasks: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """
    按 酒店 + 日期 分组。
    不传 --hotel-name 时，可能会读取多个酒店任务，不能把不同酒店混在同一批提交。
    """
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        grouped[(task["hotel_name"], task["startDate"], task["endDate"])].append(task)
    return dict(grouped)


def run_mysql_price_tasks(
    page,
    hotel_name: str,
    check_seconds: int,
    dry_run: bool = False,
    task_id: int | None = None,
) -> bool:
    tasks = load_mysql_tasks(hotel_name, task_id)
    if not tasks:
        if hotel_name:
            print(f"没有待执行的 MySQL 携程调价任务。酒店: {hotel_name}")
        else:
            print("没有待执行的 MySQL 携程调价任务。")
        return True

    if hotel_name:
        print(f"读取到待执行任务: {len(tasks)} 条，酒店: {hotel_name}")
    else:
        hotel_count = len({task["hotel_name"] for task in tasks})
        print(f"读取到待执行任务: {len(tasks)} 条，涉及酒店: {hotel_count} 家")

    all_ok = True
    grouped = group_tasks_by_date(tasks)

    for date_key, group_rows in grouped.items():
        print(f"\n处理日期批次: {date_key}")

        cipher_map = {
            row["productId"]: row["product_cipher"]
            for row in group_rows
        }

        try:
            query_payload, setting_data = query_price_setting(page, group_rows, cipher_map)

            need_change_rows = []
            for row in group_rows:
                current_price = get_current_price(setting_data, row["productId"])
                target_price = row["price"]

                info = {
                    "task_id": row["id"],
                    "hotel_name": row["hotel_name"],
                    "ota_product_id": row["productId"],
                    "business_date": row["startDate"],
                    "当前价": current_price,
                    "目标价": target_price,
                }
                print(pretty(info))

                if money_equal(current_price, target_price):
                    print("✅ 当前价格已一致，不调价")
                    if not dry_run:
                        update_task_status(row["id"], "SUCCESS", "当前价格已一致")
                else:
                    need_change_rows.append(row)

            if not need_change_rows:
                continue

            print("\n以下任务需要真实改价，将自动提交携程：")
            for row in need_change_rows:
                print(
                    pretty(
                        {
                            "task_id": row["id"],
                            "hotel_name": row["hotel_name"],
                            "ota_product_id": row["productId"],
                            "business_date": row["startDate"],
                            "target_sale_price": row["price"],
                        }
                    )
                )

            change_cipher_map = {
                row["productId"]: row["product_cipher"]
                for row in need_change_rows
            }

            change_query_payload, change_setting_data = query_price_setting(
                page,
                need_change_rows,
                change_cipher_map,
            )
            set_payload = build_set_payload(
                change_query_payload,
                need_change_rows,
                change_setting_data,
            )

            if dry_run:
                print("DRY RUN：已完成当前价查询和调价 payload 构造，未提交真实调价。")
                continue

            print(f"\n提交调价，商品数: {len(need_change_rows)}")
            result = submit_set_room_price(page, set_payload)
            print(f"{SET_ROOM_PRICE} 返回：")
            print(pretty(result.get("data")))

            if check_seconds > 0:
                print(f"等待 {check_seconds} 秒后回读验证...")
                time.sleep(check_seconds)

            _, check_data = query_price_setting(page, need_change_rows, change_cipher_map)

            for row in need_change_rows:
                after_price = get_current_price(check_data, row["productId"])
                ok = money_equal(after_price, row["price"])
                print(
                    pretty(
                        {
                            "task_id": row["id"],
                            "ota_product_id": row["productId"],
                            "目标价": row["price"],
                            "回读价": after_price,
                            "ok": ok,
                        }
                    )
                )

                if ok:
                    update_task_status(row["id"], "SUCCESS", "调价成功")
                else:
                    update_task_status(row["id"], "FAILED", f"回读价未生效: {after_price}")
                    all_ok = False

        except Exception as exc:
            all_ok = False
            print(f"❌ 日期批次失败: {date_key}")
            print(str(exc))
            for row in group_rows:
                update_task_status(row["id"], "FAILED", str(exc))

    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="携程 eBooking MySQL 批量调价")
    parser.add_argument("--hotel-name", default="", help="酒店名称；不填则自动读取全部 PENDING 任务")
    parser.add_argument("--task-id", type=int, help="只执行指定的 PENDING/EXECUTING 任务")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--cookie-env", default="CTRIP_COOKIE")
    parser.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--check-seconds", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true", help="只查询当前价并构造调价 payload，不提交真实调价")
    args = parser.parse_args()

    cookie_file = resolve_near_script(args.cookie_file)

    cookie_header = os.environ.get(args.cookie_env, "").strip()
    if not cookie_header and os.path.exists(cookie_file):
        cookie_header = load_cookie_from_file(cookie_file)
        print(f"已从文件读取 cookie: {cookie_file}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(Path(args.profile).expanduser()),
            headless=args.headless,
            viewport={"width": 1470, "height": 956},
        )

        if cookie_header:
            context.add_cookies(parse_cookie_header(cookie_header))

        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        page.evaluate(POST_HELPER_JS)
        page.wait_for_timeout(1500)

        ok = run_mysql_price_tasks(
            page=page,
            hotel_name=args.hotel_name,
            check_seconds=args.check_seconds,
            dry_run=args.dry_run,
            task_id=args.task_id,
        )

        context.close()
        return 0 if ok else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        raise SystemExit(130)
