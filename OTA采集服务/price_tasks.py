from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pymysql
import runner


ROOT = Path(__file__).resolve().parent
PRICE_DIR = ROOT.parent / "正式数据抓取-PMS（别样红）" / "ota调价"
LOG_DIR = ROOT / "logs"
PAGE_CACHE_SECONDS = 60
_PAGE_CACHE: dict[str, Any] = {"loaded_at": 0.0, "products": None, "tasks": None, "refreshing": False}
_CACHE_LOCK = threading.RLock()

PLATFORMS = {
    "meituan": {
        "label": "美团",
        "mapping": "meituan_ota_goods_price_mapping",
        "tasks": "meituan_price_task",
        "script": "meituan_change_price.py",
    },
    "ctrip": {
        "label": "携程",
        "mapping": "ctrip_ota_goods_price_mapping",
        "tasks": "ctrip_price_task",
        "script": "ctrip_change_price.py",
    },
}


def connection(settings: dict[str, Any]):
    cfg = settings.get("mysql") or {}
    return pymysql.connect(
        host=cfg.get("host"),
        port=int(cfg.get("port", 3306)),
        user=cfg.get("user"),
        password=cfg.get("password"),
        database=cfg.get("database"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def list_products(settings: dict[str, Any], platform: str, conn=None) -> list[dict[str, Any]]:
    info = require_platform(platform)
    extra = ", product_cipher" if platform == "ctrip" else ""
    sql = f"""
    SELECT ota_product_id, room_type_name, ota_product_name, ota_sale_price,
           business_date, snapshot_time {extra}
    FROM {info['mapping']}
    WHERE ota_product_id IS NOT NULL
    ORDER BY snapshot_time DESC
    LIMIT 300
    """
    owns_connection = conn is None
    conn = conn or connection(settings)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(sql)
                rows = cur.fetchall()
            except pymysql.ProgrammingError as exc:
                if exc.args and exc.args[0] == 1146:
                    return []
                raise
    finally:
        if owns_connection:
            conn.close()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        product_id = str(row.get("ota_product_id") or "")
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)
        row["ota_product_id"] = product_id
        row["display_name"] = row.get("room_type_name") or row.get("ota_product_name") or product_id
        result.append(row)
    return result


def list_tasks(settings: dict[str, Any], limit: int = 100, conn=None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    owns_connection = conn is None
    conn = conn or connection(settings)
    try:
        cur = conn.cursor()
        try:
            for platform, info in PLATFORMS.items():
                try:
                    cur.execute(
                        f"""
                        SELECT id, hotel_id, hotel_name, ota_product_id, room_type_name, business_date,
                               target_sale_price, execute_status, review_status, approval_id,
                               approved_at, queued_at, created_at
                        FROM {info['tasks']}
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                except pymysql.ProgrammingError as exc:
                    if exc.args and exc.args[0] == 1146:
                        continue
                    raise
                for row in cur.fetchall():
                    row["platform"] = platform
                    row["platform_label"] = info["label"]
                    result.append(row)
        finally:
            cur.close()
    finally:
        if owns_connection:
            conn.close()
    return sorted(result, key=lambda item: (item.get("created_at") or datetime.min, item["id"]), reverse=True)[:limit]


def page_data(
    settings: dict[str, Any],
    force: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    with _CACHE_LOCK:
        fresh = time.monotonic() - float(_PAGE_CACHE["loaded_at"]) < PAGE_CACHE_SECONDS
        cached = _PAGE_CACHE["products"] is not None
        if not force and cached:
            if not fresh and not _PAGE_CACHE["refreshing"]:
                _PAGE_CACHE["refreshing"] = True
                threading.Thread(target=_refresh_page_cache, args=(settings,), daemon=True).start()
            return _PAGE_CACHE["products"], _PAGE_CACHE["tasks"]

    return _load_page_data(settings)


def _load_page_data(
    settings: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    conn = connection(settings)
    try:
        products = {platform: list_products(settings, platform, conn) for platform in PLATFORMS}
        tasks = list_tasks(settings, conn=conn)
    finally:
        conn.close()
    with _CACHE_LOCK:
        _PAGE_CACHE.update(loaded_at=time.monotonic(), products=products, tasks=tasks, refreshing=False)
    return products, tasks


def _refresh_page_cache(settings: dict[str, Any]) -> None:
    try:
        _load_page_data(settings)
    except Exception:
        with _CACHE_LOCK:
            _PAGE_CACHE["refreshing"] = False


def cache_task(task: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        if _PAGE_CACHE["tasks"] is None:
            return
        tasks = [item for item in _PAGE_CACHE["tasks"] if not (
            item["platform"] == task["platform"] and int(item["id"]) == int(task["id"])
        )]
        tasks.insert(0, task)
        _PAGE_CACHE["tasks"] = tasks
        _PAGE_CACHE["loaded_at"] = time.monotonic()


def remove_cached_task(platform: str, task_id: int) -> None:
    with _CACHE_LOCK:
        if _PAGE_CACHE["tasks"] is None:
            return
        _PAGE_CACHE["tasks"] = [
            item for item in _PAGE_CACHE["tasks"]
            if not (item["platform"] == platform and int(item["id"]) == task_id)
        ]
        _PAGE_CACHE["loaded_at"] = time.monotonic()


def invalidate_page_cache() -> None:
    with _CACHE_LOCK:
        _PAGE_CACHE["loaded_at"] = 0.0
        _PAGE_CACHE["products"] = None
        _PAGE_CACHE["tasks"] = None


def create_task(
    settings: dict[str, Any],
    platform: str,
    product_id: str,
    business_date: str,
    target_price: str,
) -> int:
    info = require_platform(platform)
    task_date = date.fromisoformat(business_date)
    if task_date < date.today():
        raise ValueError("调价日期不能早于今天")
    price = round(float(target_price), 2)
    if price <= 0:
        raise ValueError("目标价必须大于 0")

    products = {item["ota_product_id"]: item for item in list_products(settings, platform)}
    product = products.get(str(product_id))
    if not product:
        raise ValueError("商品不在最新商品映射中，请先运行调价商品采集")

    platform_cfg = settings.get(platform) or {}
    hotel_name = str(platform_cfg.get("hotel_name") or "").strip()
    hotel_id = str((settings.get("hotel") or {}).get("hotel_id") or "").strip()
    if not hotel_name:
        raise ValueError("请先在配置页填写酒店名称")
    room_name = str(product.get("display_name") or product_id)[:200]

    with connection(settings) as conn, conn.cursor() as cur:
        if platform == "ctrip":
            cipher = str(product.get("product_cipher") or "").strip()
            if not cipher:
                raise ValueError("携程商品缺少 product_cipher，请重新采集调价商品")
            sql = f"""
            INSERT INTO {info['tasks']}
              (hotel_id, hotel_name, channel_source, ota_product_id, room_type_name, business_date,
               target_sale_price, execute_status, product_cipher)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING', %s)
            ON DUPLICATE KEY UPDATE room_type_name=VALUES(room_type_name),
              target_sale_price=VALUES(target_sale_price), product_cipher=VALUES(product_cipher),
              execute_status='PENDING', review_status='PENDING', approval_id=NULL,
              approved_by=NULL, approved_at=NULL, queued_at=NULL
            """
            params = (hotel_id, hotel_name, platform, product_id, room_name, task_date, price, cipher)
        else:
            sql = f"""
            INSERT INTO {info['tasks']}
              (hotel_id, hotel_name, channel_source, ota_product_id, room_type_name, business_date,
               target_sale_price, execute_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING')
            ON DUPLICATE KEY UPDATE room_type_name=VALUES(room_type_name),
              target_sale_price=VALUES(target_sale_price), execute_status='PENDING',
              review_status='PENDING', approval_id=NULL, approved_by=NULL,
              approved_at=NULL, queued_at=NULL
            """
            params = (hotel_id, hotel_name, platform, product_id, room_name, task_date, price)
        cur.execute(sql, params)
        task_id = int(cur.lastrowid)
        if not task_id:
            cur.execute(
                f"SELECT id FROM {info['tasks']} WHERE hotel_id=%s AND hotel_name=%s AND ota_product_id=%s AND business_date=%s",
                (hotel_id, hotel_name, product_id, task_date),
            )
            task_id = int(cur.fetchone()["id"])
        conn.commit()

    if platform == "meituan":
        sync_meituan_hotel_config(settings, hotel_name)
    cache_task(
        {
            "id": task_id,
            "hotel_id": hotel_id,
            "hotel_name": hotel_name,
            "ota_product_id": str(product_id),
            "room_type_name": room_name,
            "business_date": task_date,
            "target_sale_price": price,
            "execute_status": "PENDING",
            "review_status": "PENDING",
            "approval_id": None,
            "created_at": datetime.now(),
            "platform": platform,
            "platform_label": info["label"],
        }
    )
    return task_id


def cancel_task(settings: dict[str, Any], platform: str, task_id: int) -> bool:
    info = require_platform(platform)
    with connection(settings) as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {info['tasks']} WHERE id=%s AND execute_status='PENDING'", (task_id,))
        changed = cur.rowcount > 0
        conn.commit()
    if changed:
        remove_cached_task(platform, task_id)
    return changed


def set_task_status(settings: dict[str, Any], platform: str, task_id: int, status: str) -> None:
    info = require_platform(platform)
    with connection(settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {info['tasks']} SET execute_status=%s WHERE id=%s", (status, task_id))
        conn.commit()


def claim_task(settings: dict[str, Any], platform: str, task_id: int) -> bool:
    info = require_platform(platform)
    with connection(settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {info['tasks']} SET execute_status='EXECUTING' WHERE id=%s AND execute_status='PENDING'",
            (task_id,),
        )
        claimed = cur.rowcount == 1
        conn.commit()
    return claimed


def launch_task(settings: dict[str, Any], platform: str, task_id: int, dry_run: bool) -> Path:
    info = require_platform(platform)
    task = next(
        (item for item in list_tasks(settings, 300) if item["platform"] == platform and int(item["id"]) == task_id),
        None,
    )
    if not task or task.get("execute_status") != "PENDING":
        raise ValueError("只能预览或执行 PENDING 任务")

    script = PRICE_DIR / info["script"]
    if not script.exists():
        raise FileNotFoundError(f"调价脚本不存在：{script}")
    if platform == "meituan":
        sync_meituan_hotel_config(settings, task["hotel_name"])

    command = [
        str(runner.python_path(settings)),
        str(script),
        "--task-id",
        str(task_id),
        "--hotel-name",
        str(task["hotel_name"]),
        "--headless",
        "--check-seconds",
        "0" if dry_run else "10",
    ]
    if dry_run:
        command.append("--dry-run")

    env = runner.build_env(settings)
    env["PYTHONIOENCODING"] = "utf-8"
    if platform == "meituan":
        env["MEITUAN_COOKIE"] = str((settings.get("meituan") or {}).get("me_cookie") or "")
    else:
        env["CTRIP_COOKIE"] = str((settings.get("ctrip") or {}).get("cookie") or "")
    if not env["MEITUAN_COOKIE" if platform == "meituan" else "CTRIP_COOKIE"]:
        raise ValueError(f"{info['label']} Cookie 为空")

    if not dry_run:
        if not claim_task(settings, platform, task_id):
            raise ValueError("任务已被其他执行器领取或状态已改变")
        task["execute_status"] = "EXECUTING"
        cache_task(task)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "preview" if dry_run else "execute"
    log_path = LOG_DIR / f"price_{platform}_{task_id}_{suffix}_{datetime.now():%Y%m%d_%H%M%S}.log"
    log = log_path.open("w", encoding="utf-8")
    try:
        subprocess.Popen(
            command,
            cwd=str(PRICE_DIR),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        if not dry_run:
            set_task_status(settings, platform, task_id, "FAILED")
        raise
    finally:
        log.close()
    return log_path


def sync_meituan_hotel_config(settings: dict[str, Any], hotel_name: str) -> None:
    cfg = settings.get("meituan") or {}
    path = PRICE_DIR / "meituan_hotel_config.json"
    data: dict[str, Any] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    data[hotel_name] = {"poi_id": str(cfg.get("poi_id") or ""), "partner_id": int(cfg.get("partner_id") or 0)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def require_platform(platform: str) -> dict[str, str]:
    if platform not in PLATFORMS:
        raise ValueError("不支持的平台")
    return PLATFORMS[platform]
