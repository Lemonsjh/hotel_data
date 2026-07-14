#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
美团 eBooking MySQL 调价脚本。

流程：
1. 打开美团批量改房价页
2. 捕获当前页面最新 delayGoodsList / queryListAndTag
3. 从 MySQL 表 调价任务表 读取 PENDING 调价任务
4. 按 ota_product_id 找到当前页面最新商品信息
5. 调用 calcPriceV2 查询美团当前价格
6. 当前价 != MySQL target_sale_price 时自动调用 updatePriceV2
7. 成功后把任务 execute_status 更新为 SUCCESS，失败更新为 FAILED
8. 不需要输入 YES
9. 不需要命令行传 poi_id / partner_id，自动从 meituan_hotel_config.json 按酒店名读取

配置文件 meituan_hotel_config.json 示例：
{
  "酒店名称": {
    "poi_id": "美团 poiId",
    "partner_id": "美团 partnerId"
  }
}

依赖：
pip install pymysql playwright
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import pymysql
from playwright.sync_api import Error as PlaywrightError, sync_playwright

sys.stdout.reconfigure(encoding='utf-8')


DEFAULT_URL = "https://me.meituan.com/ebooking/merchant/product/batch-price"
UPDATE_URL = "/api/gw/v1/product/price/updatePriceV2"
CALC_URL = "/api/gw/v1/product/price/separate/calcPriceV2"
DEFAULT_HOTEL_CONFIG_FILE = "./meituan_hotel_config.json"


def goto_with_retry(page: Any, url: str, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return
        except PlaywrightError as exc:
            if attempt == attempts:
                raise
            wait_seconds = attempt * 2
            print(f"打开美团调价页失败，第 {attempt}/{attempts} 次：{exc}；{wait_seconds} 秒后重试")
            time.sleep(wait_seconds)


DB_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", os.environ.get("HOTEL_OTA_MYSQL_HOST", "127.0.0.1")),
    "port": int(os.environ.get("MYSQL_PORT", os.environ.get("HOTEL_OTA_MYSQL_PORT", "3306"))),
    "user": os.environ.get("MYSQL_USER", os.environ.get("HOTEL_OTA_MYSQL_USER", "")),
    "password": os.environ.get("MYSQL_PASSWORD", os.environ.get("HOTEL_OTA_MYSQL_PASSWORD", "")),
    "database": os.environ.get("MYSQL_DATABASE", os.environ.get("HOTEL_OTA_MYSQL_DATABASE", "")),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}


POST_HELPER_JS = r"""
window.__mtPostJson = async function(url, payload) {
  return await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url, true);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.setRequestHeader("M-APPKEY", "fe_com.sankuai.fetalos.web.hotelfeme");
    xhr.setRequestHeader("locale", "zh-CN");
    xhr.setRequestHeader("logintype", "Epassport");
    xhr.setRequestHeader("x-Requested-With", "XMLHttpRequest");
    xhr.onload = () => {
      let data = xhr.responseText;
      try { data = JSON.parse(xhr.responseText); } catch (e) {}
      resolve({ status: xhr.status, data });
    };
    xhr.onerror = () => reject(new Error("XHR network error"));
    xhr.send(JSON.stringify(payload));
  });
};
"""


def yuan_to_api_price(price: float) -> str:
    return str(int(round(price * 100)))


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_near_script(path_text: str) -> str:
    path = Path(path_text).expanduser()
    if path.exists():
        return str(path)

    candidate = Path(__file__).resolve().parent / path_text
    if candidate.exists():
        return str(candidate)

    return str(path)


def load_hotel_config(config_file: str = DEFAULT_HOTEL_CONFIG_FILE) -> dict[str, dict[str, Any]]:
    """
    读取美团酒店配置文件。

    配置文件格式：
    {
      "贵阳智町栖筑优品酒店": {
        "poi_id": "1028118229",
        "partner_id": 4660976
      }
    }
    """
    config_path = resolve_near_script(config_file)
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"找不到酒店配置文件: {config_path}")

    data = load_json(config_path)
    if not isinstance(data, dict):
        raise ValueError("酒店配置文件格式错误，根节点必须是 JSON 对象")

    return data


def get_hotel_mt_config(
    hotel_config: dict[str, dict[str, Any]],
    hotel_name: str,
) -> tuple[str, int]:
    if hotel_name not in hotel_config:
        raise RuntimeError(f"meituan_hotel_config.json 缺少酒店配置: {hotel_name}")

    item = hotel_config[hotel_name]
    poi_id = str(item.get("poi_id", "")).strip()
    partner_id = item.get("partner_id")

    if not poi_id or partner_id in (None, ""):
        raise RuntimeError(f"酒店配置不完整: {hotel_name}，需要 poi_id 和 partner_id")

    return poi_id, int(partner_id)


def normalize_date(date_text: Any) -> str:
    date_text = str(date_text).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_text, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return date_text


def iter_goods_from_delay_goods(data: Any):
    if isinstance(data, dict):
        if isinstance(data.get("goodsList"), list):
            room_base_info = data.get("roomBaseInfo") or {}
            for goods in data["goodsList"]:
                if isinstance(goods, dict):
                    item = dict(goods)
                    if room_base_info:
                        item.setdefault("roomBaseInfo", room_base_info)
                    yield item

        for value in data.values():
            yield from iter_goods_from_delay_goods(value)

    elif isinstance(data, list):
        for value in data:
            yield from iter_goods_from_delay_goods(value)


def find_goods_in_delay_goods(
    data: Any,
    *,
    goods_id: int | None,
    goods_name: str | None = None,
    room_type_name: str | None = None,
) -> dict[str, Any]:
    matches = []
    room_type_name = str(room_type_name or "").strip()

    for goods in iter_goods_from_delay_goods(data):
        if goods_id is not None and int(goods.get("goodsId", 0)) != int(goods_id):
            continue

        goods_name_text = str(goods.get("goodsName", "")).strip()
        room_name_text = str(goods.get("roomBaseInfo", {}).get("roomName", "")).strip()

        if goods_name and goods_name not in goods_name_text:
            continue

        if room_type_name:
            # 新表没有 goods_id，统一按房型名称匹配。
            # 优先匹配 roomBaseInfo.roomName；兼容部分接口只返回 goodsName 的情况。
            if room_type_name != room_name_text and room_type_name not in goods_name_text:
                continue

        matches.append(goods)

    if not matches:
        raise RuntimeError(
            f"没有从 delayGoodsList 中找到匹配商品 "
            f"goodsId={goods_id}, room_type_name={room_type_name or None}"
        )

    if len(matches) > 1:
        print("找到多个匹配商品，将使用第一个：")
        print(pretty([
            {
                "goodsId": item.get("goodsId"),
                "goodsName": item.get("goodsName"),
                "sellChannel": item.get("sellChannel"),
                "roomName": item.get("roomBaseInfo", {}).get("roomName"),
            }
            for item in matches[:10]
        ]))

    return matches[0]


def capture_delay_goods_response(
    page,
    timeout_ms: int,
    goods_id: int | None = None,
    goods_name: str | None = None,
) -> dict[str, Any] | None:
    records: list[dict[str, Any]] = []
    checked_urls = []

    def on_response(response):
        url = response.url

        if "/api/" not in url:
            return

        if "delayGoodsList" not in url and "queryListAndTag" not in url:
            return

        checked_urls.append(url)

        try:
            text = response.text()
        except Exception:
            return

        if "goodsId" not in text:
            return

        try:
            data = json.loads(text)
        except Exception:
            return

        if goods_id is not None or goods_name:
            try:
                find_goods_in_delay_goods(data, goods_id=goods_id, goods_name=goods_name)
            except RuntimeError:
                print(f"跳过商品接口，未匹配目标商品: {url}")
                return

        records.append(data)
        print(f"✅ 捕获商品接口: {url}")

    page.on("response", on_response)
    deadline = time.time() + timeout_ms / 1000

    while time.time() < deadline and not records:
        page.wait_for_timeout(500)

    page.remove_listener("response", on_response)

    if not records and checked_urls:
        print("\n📋 已检查的 API 接口:")
        for u in checked_urls[:5]:
            print(f"  - {u}")
        if len(checked_urls) > 5:
            print(f"  - ... 还有 {len(checked_urls) - 5} 个接口")

    return records[-1] if records else None


def parse_cookie_header(cookie_header: str) -> list[dict[str, Any]]:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    cookies = []

    for name, morsel in cookie.items():
        cookies.append({
            "name": name,
            "value": morsel.value,
            "domain": ".meituan.com",
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        })

    return cookies


def load_cookie_from_file(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def build_goods_update_item(
    *,
    goods_base_info: dict[str, Any],
    date: str,
    price: float,
) -> dict[str, Any]:
    api_price = yuan_to_api_price(price)

    return {
        "goodsBaseInfo": goods_base_info,
        "ratioConfig": {
            "ratioType": None,
            "ratioChange": False,
            "newRatio": None,
        },
        "priceRecordWay": 8,
        "weekDiff": False,
        "calcPriceUnifiedDateModel": {
            "dates": [{"startDate": date, "endDate": date}],
            "calcPriceWeekModels": [
                {
                    "inWeek": [1, 2, 3, 4, 5, 6, 7],
                    "calcPriceInfo": {
                        "salePrice": {
                            "operateType": 6,
                            "operateNum": api_price,
                        },
                        "basePrice": {
                            "operateType": 3,
                            "operateNum": "",
                        },
                        "subPrice": {
                            "operateType": 3,
                            "operateNum": "",
                        },
                    },
                    "calcPriceFactorInfos": None,
                }
            ],
        },
    }


def build_update_payload(
    *,
    poi_id: str,
    partner_id: int,
    currency: str,
    goods_updates: list[tuple[dict[str, Any], float]],
    date: str,
    create_flag: bool,
) -> dict[str, Any]:
    return {
        "poiId": str(poi_id),
        "partnerId": partner_id,
        "currency": currency,
        "createFlag": create_flag,
        "goodsList": [
            build_goods_update_item(
                goods_base_info=goods_base_info,
                date=date,
                price=price,
            )
            for goods_base_info, price in goods_updates
        ],
        "extendParam": {},
    }


def build_check_payload(update_payload: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(update_payload, ensure_ascii=False))
    payload.pop("createFlag", None)
    payload.pop("extendParam", None)

    for item in payload.get("goodsList", []):
        for week_model in item["calcPriceUnifiedDateModel"]["calcPriceWeekModels"]:
            week_model["calcPriceInfo"]["salePrice"] = {
                "operateType": 3,
                "operateNum": "",
            }
            week_model["calcPriceFactorInfos"] = []

    return payload


def post_in_page(page, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    page.evaluate(POST_HELPER_JS)

    return page.evaluate(
        """
        async ({url, payload}) => {
          if (typeof window.__mtPostJson !== "function") {
            throw new Error("window.__mtPostJson 注入失败");
          }
          return await window.__mtPostJson(url, payload);
        }
        """,
        {"url": url, "payload": payload},
    )


def extract_current_prices(calc_response: dict[str, Any]) -> list[dict[str, Any]]:
    data = calc_response.get("data")
    if not isinstance(data, dict):
        return []

    goods_details = data.get("data", {}).get("goodsDetails", [])
    rows = []

    for item in goods_details:
        week_info = item["unifiedDatePriceInfos"]["weekPriceInfos"][0]
        rows.append({
            "goodsId": item["goodsBaseInfo"]["goodsId"],
            "当前卖价": week_info["originalPriceInfo"]["salePrice"],
            "预览卖价": week_info["priceInfo"]["salePrice"],
            "当前底价": week_info["originalPriceInfo"]["basePrice"],
        })

    return rows


def load_mysql_tasks(hotel_name: str | None = None, task_id: int | None = None) -> list[dict[str, Any]]:
    conn = pymysql.connect(**DB_CONFIG)

    sql = """
    SELECT
        id,
        hotel_name,
        ota_product_id,
        room_type_name,
        business_date,
        target_sale_price
    FROM meituan_price_task
    """

    params = []
    if task_id is not None:
        sql += " WHERE id = %s AND execute_status IN ('PENDING', 'EXECUTING')"
        params.append(task_id)
    else:
        sql += " WHERE execute_status = 'PENDING'"
    if hotel_name:
        sql += " AND hotel_name = %s"
        params.append(hotel_name)

    sql += " ORDER BY business_date, ota_product_id"

    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
    finally:
        conn.close()

    return rows


def update_task_status(task_id: int, status: str) -> None:
    conn = pymysql.connect(**DB_CONFIG)

    sql = """
    UPDATE meituan_price_task
    SET execute_status = %s
    WHERE id = %s
    """

    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (status, task_id))
        conn.commit()
    finally:
        conn.close()


def run_mysql_price_tasks(
    page,
    delay_goods_data: dict[str, Any],
    *,
    tasks: list[dict[str, Any]],
    hotel_name: str | None,
    hotel_config_file: str,
    currency: str,
    create_flag: bool,
    check_seconds: int,
    dry_run: bool = False,
) -> bool:
    if not tasks:
        print("没有需要调价的任务。")
        return True

    hotel_count = len({task["hotel_name"] for task in tasks})
    print(f"读取到 {len(tasks)} 条 MySQL 待执行调价任务，涉及酒店 {hotel_count} 家")

    hotel_config = load_hotel_config(hotel_config_file)
    plans_by_hotel_date = defaultdict(list)

    for task in tasks:
        goods_id = int(task["ota_product_id"])
        room_type_name = str(task.get("room_type_name") or "").strip()

        try:
            goods = find_goods_in_delay_goods(
                delay_goods_data,
                goods_id=goods_id,
                goods_name=None,
                room_type_name=None,
            )
        except RuntimeError:
            print(f"❌ 当前页面没有找到商品ID：{goods_id}，房型：{room_type_name or '-'}")
            update_task_status(task["id"], "FAILED")
            continue

        date = normalize_date(task["business_date"])
        price = float(task["target_sale_price"])

        plans_by_hotel_date[(task["hotel_name"], date)].append({
            "task": task,
            "goods": goods,
            "price": price,
        })

    all_ok = True

    for (task_hotel_name, date), items in plans_by_hotel_date.items():
        if not items:
            continue

        try:
            poi_id, partner_id = get_hotel_mt_config(hotel_config, task_hotel_name)
        except Exception as exc:
            print(f"❌ 酒店配置错误: {task_hotel_name}，{exc}")
            for item in items:
                update_task_status(item["task"]["id"], "FAILED")
            all_ok = False
            continue

        print(f"\n=== 酒店 {task_hotel_name} / 日期 {date} ===")
        print(pretty({
            "hotel_name": task_hotel_name,
            "poi_id": poi_id,
            "partner_id": partner_id,
        }))

        goods_updates = [
            (item["goods"], item["price"])
            for item in items
        ]

        check_update_payload = build_update_payload(
            poi_id=poi_id,
            partner_id=partner_id,
            currency=currency,
            goods_updates=goods_updates,
            date=date,
            create_flag=create_flag,
        )

        check_result = post_in_page(
            page,
            CALC_URL,
            build_check_payload(check_update_payload),
        )
        current_rows = extract_current_prices(check_result)

        current_price_map = {
            int(row["goodsId"]): str(row["当前卖价"])
            for row in current_rows
        }

        need_update_items = []

        print(f"\n=== 日期 {date} 当前价格检查 ===")

        for item in items:
            task = item["task"]
            goods_id = int(item["goods"].get("goodsId"))
            target_api_price = yuan_to_api_price(item["price"])
            current_api_price = current_price_map.get(goods_id)

            print(pretty({
                "task_id": task["id"],
                "hotel_name": task["hotel_name"],
                "goodsId": goods_id,
                "ota_product_id": task["ota_product_id"],
                "room_type_name": task.get("room_type_name"),
                "date": date,
                "当前价_分": current_api_price,
                "目标价_分": target_api_price,
            }))

            if current_api_price == target_api_price:
                print("✅ 当前价格已一致，不调价")
                if not dry_run:
                    update_task_status(task["id"], "SUCCESS")
            else:
                need_update_items.append(item)

        if not need_update_items:
            continue

        update_payload = build_update_payload(
            poi_id=poi_id,
            partner_id=partner_id,
            currency=currency,
            goods_updates=[
                (item["goods"], item["price"])
                for item in need_update_items
            ],
            date=date,
            create_flag=create_flag,
        )

        print(f"\n准备自动提交调价：{task_hotel_name} / {date}，商品数 {len(need_update_items)}")
        print(pretty([
            {
                "task_id": item["task"]["id"],
                "goodsId": item["goods"].get("goodsId"),
                "ota_product_id": item["task"]["ota_product_id"],
                "room_type_name": item["task"].get("room_type_name"),
                "target_sale_price": item["price"],
                "operateNum": yuan_to_api_price(item["price"]),
            }
            for item in need_update_items
        ]))

        if dry_run:
            print("DRY RUN：已完成当前价查询和 updatePriceV2 payload 构造，未提交真实调价。")
            continue

        result = post_in_page(page, UPDATE_URL, update_payload)
        print("updatePriceV2 返回：")
        print(pretty(result))

        ok = (
            result.get("status") == 200
            and isinstance(result.get("data"), dict)
            and result["data"].get("success") is True
        )

        if not ok:
            print("❌ updatePriceV2 未返回 success=true")
            for item in need_update_items:
                update_task_status(item["task"]["id"], "FAILED")
            all_ok = False
            continue

        if check_seconds > 0:
            print(f"等待 {check_seconds} 秒后复查当前价格...")
            time.sleep(check_seconds)

        verify_result = post_in_page(page, CALC_URL, build_check_payload(update_payload))
        verify_rows = extract_current_prices(verify_result)

        verify_price_map = {
            int(row["goodsId"]): str(row["当前卖价"])
            for row in verify_rows
        }

        for item in need_update_items:
            task = item["task"]
            goods_id = int(item["goods"].get("goodsId"))
            target_api_price = yuan_to_api_price(item["price"])
            current_api_price = verify_price_map.get(goods_id)

            if current_api_price == target_api_price:
                print(f"✅ task_id={task['id']} 调价成功")
                update_task_status(task["id"], "SUCCESS")
            else:
                print(f"❌ task_id={task['id']} 提交成功但复查价格不一致")
                update_task_status(task["id"], "FAILED")
                all_ok = False

    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="美团 eBooking MySQL 调价脚本")

    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--delay-goods-file", help="完整 delayGoodsList 响应 JSON，不传则从页面捕获")
    parser.add_argument("--hotel-name", help="指定酒店名称，只执行该酒店 PENDING 任务；不填则读取全部 PENDING 任务")
    parser.add_argument("--task-id", type=int, help="只执行指定的 PENDING/EXECUTING 任务")
    parser.add_argument("--hotel-config", default=DEFAULT_HOTEL_CONFIG_FILE, help="美团酒店配置文件")
    parser.add_argument("--currency", default="CNY")
    parser.add_argument("--cookie-env", default="MEITUAN_COOKIE")
    parser.add_argument("--cookie-file", default="./meituan_cookie.txt", help="cookie 文件路径，默认 ./meituan_cookie.txt")
    parser.add_argument("--profile", default="./meituan-user-data")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--create-flag", choices=["true", "false"], default="true")
    parser.add_argument("--check-seconds", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true", help="只查询当前价并构造调价 payload，不提交 updatePriceV2")

    args = parser.parse_args()
    create_flag = args.create_flag == "true"

    cookie_header = os.environ.get(args.cookie_env, "").strip()
    cookie_file = args.cookie_file

    if cookie_file and not os.path.exists(cookie_file):
        candidate = Path(__file__).resolve().parent / cookie_file
        if candidate.exists():
            cookie_file = str(candidate)

    if not cookie_header and cookie_file and os.path.exists(cookie_file):
        cookie_header = load_cookie_from_file(cookie_file)
        print(f"✅ 从文件读取 cookie: {cookie_file}")

    # 先查 MySQL 是否有 PENDING 任务。
    # 没有任务就直接退出，不打开浏览器，也不捕获商品接口。
    tasks = load_mysql_tasks(args.hotel_name, args.task_id)
    if not tasks:
        print("没有需要调价的任务。")
        return 0

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(Path(args.profile).expanduser()),
            headless=args.headless,
            viewport={"width": 1470, "height": 956},
        )

        if cookie_header:
            context.add_cookies(parse_cookie_header(cookie_header))

        page = context.new_page()
        goto_with_retry(page, args.url)
        page.evaluate(POST_HELPER_JS)

        if args.delay_goods_file:
            delay_goods_data = load_json(args.delay_goods_file)
        else:
            print("正在捕获 delayGoodsList。若未捕获到，请在浏览器里刷新页面、展开房型或进入批量改房价页。")

            delay_goods_data = capture_delay_goods_response(
                page,
                timeout_ms=15000,
                goods_id=None,
                goods_name=None,
            )

            if not delay_goods_data:
                input("还没捕获到 delayGoodsList。请手动刷新/展开房型后按回车继续等待...")
                delay_goods_data = capture_delay_goods_response(
                    page,
                    timeout_ms=30000,
                    goods_id=None,
                    goods_name=None,
                )

            if not delay_goods_data:
                print("没有捕获到 delayGoodsList。")
                return 1

        ok = run_mysql_price_tasks(
            page,
            delay_goods_data,
            tasks=tasks,
            hotel_name=args.hotel_name,
            hotel_config_file=args.hotel_config,
            currency=args.currency,
            create_flag=create_flag,
            check_seconds=args.check_seconds,
            dry_run=args.dry_run,
        )

        return 0 if ok else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        raise SystemExit(130)
