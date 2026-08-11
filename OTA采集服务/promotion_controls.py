from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = PROJECT_ROOT / "美团OTA数据采集代码"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import runner
from mysql_connection import connect_mysql

from meituan_page_capture import browser_profile_lock, cookie_entries, page_access_issue, profile_path


TABLE_NAME = "meituan_ota_promotion_performance_30d"
TASK_TABLE = "ota_promotion_control_task"
PROMOTION_PAGE_URL = (
    "https://me.meituan.com/ebooking/merchant/fullPathIframe?"
    "iUrl=https%3A%2F%2Febmidas.dianping.com%2Fshopdiy%2Faccount%2FpcCpcEntry%3F"
    "continueUrl%3D%2Fapp%2Fpeon-merchant-product-menu%2Fhtml%2Findex.html%3F"
    "continueUrl%3D%2Fapp%2Fpeon-hornet-promo%2Fhtml%2Fpromo-list.html%23menuId%3D1"
)
ACTION_CONFIG = {
    "pause": {"label": "暂停", "expected_status": "PAUSED", "expected_text": "已暂停"},
    "recover": {"label": "恢复", "expected_status": "RUNNING", "expected_text": "推广中"},
}
STATUS_ALIASES = {"PAUSE": "PAUSED"}


def normalized_promotion_status(value: Any) -> str:
    status = str(value or "UNKNOWN").strip().upper()
    return STATUS_ALIASES.get(status, status)


def current_promotions(settings: dict[str, Any]) -> list[dict[str, Any]]:
    hotel_id = runner.internal_hotel_id(settings, "meituan")
    if not hotel_id:
        raise RuntimeError("请先在配置中填写酒店 ID")

    connection = connect_database(settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT hotel_id, plan_id, plan_name, launch_id, launch_name, promotion_name, "
                f"promotion_type, promotion_status, snapshot_time FROM `{TABLE_NAME}` "
                "WHERE hotel_id=%s ORDER BY snapshot_time DESC",
                (hotel_id,),
            )
            columns = [column[0] for column in cursor.description or []]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()

    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        row["promotion_status"] = normalized_promotion_status(row.get("promotion_status"))
        launch_id = str(row.get("launch_id") or "").strip()
        if launch_id and launch_id not in latest:
            latest[launch_id] = row
    return list(latest.values())


def find_promotion(settings: dict[str, Any], launch_id: str) -> dict[str, Any]:
    launch_id = str(launch_id or "").strip()
    for promotion in current_promotions(settings):
        if str(promotion.get("launch_id")) == launch_id:
            return promotion
    raise RuntimeError("未找到该推广记录，请先运行美团近30天推广效果采集")


def enqueue_control_task(settings: dict[str, Any], promotion: dict[str, Any], action: str) -> int:
    config = ACTION_CONFIG.get(action)
    if not config:
        raise RuntimeError("不支持的推广操作")
    launch_id = str(promotion.get("launch_id") or "").strip()
    if not launch_id.isdigit():
        raise RuntimeError("推广 launch_id 无效")
    current = normalized_promotion_status(promotion.get("promotion_status"))
    if current == config["expected_status"]:
        raise RuntimeError(f"推广当前已是{config['label']}后的状态，无需创建任务")
    if current not in {"RUNNING", "PAUSED"}:
        raise RuntimeError(f"当前推广状态不支持{config['label']}：{current or 'UNKNOWN'}")

    hotel_id = runner.internal_hotel_id(settings, "meituan")
    connection = connect_database(settings, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id FROM `{TASK_TABLE}` WHERE hotel_id=%s AND platform='meituan' "
                "AND launch_id=%s AND action=%s AND status IN ('pending', 'processing') LIMIT 1",
                (hotel_id, launch_id, action),
            )
            if cursor.fetchone():
                raise RuntimeError("相同推广操作已在待执行队列中")
            cursor.execute(
                f"INSERT INTO `{TASK_TABLE}` (hotel_id, platform, launch_id, action) VALUES (%s, 'meituan', %s, %s)",
                (hotel_id, launch_id, action),
            )
            task_id = int(cursor.lastrowid)
        connection.commit()
        return task_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def recent_control_tasks(settings: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    hotel_id = runner.internal_hotel_id(settings, "meituan")
    connection = connect_database(settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id, launch_id, action, status, error_message, created_at, executed_at "
                f"FROM `{TASK_TABLE}` WHERE hotel_id=%s AND platform='meituan' ORDER BY id DESC LIMIT %s",
                (hotel_id, max(1, limit)),
            )
            columns = [column[0] for column in cursor.description or []]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()


def claim_control_tasks(settings: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    hotel_id = runner.internal_hotel_id(settings, "meituan")
    connection = connect_database(settings, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id, launch_id, action FROM `{TASK_TABLE}` WHERE hotel_id=%s "
                "AND platform='meituan' AND status='pending' ORDER BY id LIMIT %s",
                (hotel_id, max(1, limit)),
            )
            claimed = []
            for task_id, launch_id, action in cursor.fetchall():
                cursor.execute(
                    f"UPDATE `{TASK_TABLE}` SET status='processing', error_message=NULL "
                    "WHERE id=%s AND status='pending'",
                    (task_id,),
                )
                if cursor.rowcount:
                    claimed.append({"id": task_id, "launch_id": str(launch_id), "action": str(action)})
            return claimed
    finally:
        connection.close()


def finish_control_task(settings: dict[str, Any], task_id: int, status: str, error: str | None = None) -> None:
    connection = connect_database(settings, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE `{TASK_TABLE}` SET status=%s, error_message=%s, executed_at=%s WHERE id=%s",
                (status, None if status == "success" else (error or "operation failed")[:500], datetime.now(), task_id),
            )
    finally:
        connection.close()


def fail_interrupted_control_tasks(settings: dict[str, Any]) -> None:
    hotel_id = runner.internal_hotel_id(settings, "meituan")
    connection = connect_database(settings, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE `{TASK_TABLE}` SET status='failed', error_message=%s "
                "WHERE hotel_id=%s AND platform='meituan' AND status='processing'",
                ("Previous promotion operation was interrupted before confirmation", hotel_id),
            )
    finally:
        connection.close()


def control_promotion(settings: dict[str, Any], promotion: dict[str, Any], action: str) -> str:
    config = ACTION_CONFIG.get(action)
    if not config:
        raise RuntimeError("不支持的推广操作")
    launch_id = str(promotion.get("launch_id") or "").strip()
    if not launch_id.isdigit():
        raise RuntimeError("推广 launch_id 无效")

    current = normalized_promotion_status(promotion.get("promotion_status"))
    if current == config["expected_status"]:
        return f"推广当前已是{config['label']}后的状态，无需重复操作"
    if current not in {"RUNNING", "PAUSED"}:
        raise RuntimeError(f"当前推广状态不支持{config['label']}：{current or 'UNKNOWN'}")

    try:
        with browser_profile_lock(), sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path()),
                channel="msedge",
                headless=True,
                chromium_sandbox=True,
                locale="zh-CN",
            )
            try:
                add_config_cookies(context, settings)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(PROMOTION_PAGE_URL, wait_until="domcontentloaded", timeout=60_000)
                frame, row = wait_for_launch(page, launch_id)
                if config["expected_text"] in row.inner_text(timeout=2_000):
                    update_snapshot_status(settings, promotion, config["expected_status"])
                    return f"推广当前已是{config['label']}后的状态，无需重复操作"

                row.click(timeout=10_000)
                button = wait_for_action_button(frame, config["label"])
                button.click(timeout=10_000)
                confirm_modal(frame)
                wait_for_status(row, config["expected_text"])
            finally:
                context.close()
    except Exception:
        append_audit_log(launch_id, action, "failed")
        raise

    update_snapshot_status(settings, promotion, config["expected_status"])
    append_audit_log(launch_id, action, "success")
    return f"推广已{config['label']}，本地推广快照状态已同步"


def add_config_cookies(context: Any, settings: dict[str, Any]) -> None:
    meituan = settings.get("meituan") or {}
    me_cookie = str(meituan.get("me_cookie") or "")
    eb_cookie = str(meituan.get("eb_cookie") or "")
    cookies = cookie_entries(me_cookie, "https://me.meituan.com/")
    cookies += cookie_entries(eb_cookie, "https://eb.meituan.com/")
    if cookies:
        context.add_cookies(cookies)


def wait_for_launch(page: Page, launch_id: str) -> tuple[Any, Any]:
    selector = f"tr[data-row-key='{launch_id}']"
    for _ in range(90):
        for frame in page.frames:
            try:
                row = frame.locator(selector)
                if row.count() and row.first.is_visible():
                    return frame, row.first
            except Exception:
                continue
        if issue := page_access_issue(page):
            raise RuntimeError(f"推广页需要人工处理：{issue}")
        page.wait_for_timeout(500)
    raise RuntimeError("推广页未找到目标推广，可能已变更或登录会话失效")


def wait_for_action_button(frame: Any, label: str) -> Any:
    for _ in range(20):
        button = frame.get_by_role("button", name=label, exact=True)
        try:
            if button.count() and button.first.is_visible():
                return button.first
        except Exception:
            pass
        frame.page.wait_for_timeout(500)
    raise RuntimeError(f"推广页未显示“{label}”按钮")


def confirm_modal(frame: Any) -> None:
    for label in ("确定", "确认"):
        button = frame.get_by_role("button", name=label, exact=True)
        try:
            if button.count() and button.last.is_visible():
                button.last.click(timeout=5_000)
                return
        except Exception:
            continue


def wait_for_status(row: Any, expected_text: str) -> None:
    for _ in range(40):
        try:
            if expected_text in row.inner_text(timeout=1_000):
                return
        except Exception:
            pass
        row.page.wait_for_timeout(500)
    raise RuntimeError("推广状态请求未得到预期结果，请到美团后台核对")


def update_snapshot_status(settings: dict[str, Any], promotion: dict[str, Any], status: str) -> None:
    hotel_id = runner.internal_hotel_id(settings, "meituan")
    connection = connect_database(settings, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE `{TABLE_NAME}` SET promotion_status=%s, snapshot_time=%s "
                "WHERE hotel_id=%s AND plan_id=%s AND launch_id=%s",
                (
                    status,
                    datetime.now(),
                    hotel_id,
                    promotion.get("plan_id"),
                    promotion.get("launch_id"),
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def append_audit_log(launch_id: str, action: str, result: str) -> None:
    runner.LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} launch_id={launch_id} action={action} result={result}\n"
    with (runner.LOG_DIR / "meituan_promotion_control.log").open("a", encoding="utf-8") as file:
        file.write(line)


def connect_database(settings: dict[str, Any], *, autocommit: bool = False) -> Any:
    mysql = settings.get("mysql") or {}
    return connect_mysql(
        {
            "host": mysql.get("host", "127.0.0.1"),
            "port": int(mysql.get("port") or 3306),
            "user": mysql.get("user", ""),
            "password": mysql.get("password", ""),
            "database": mysql.get("database", ""),
            "charset": "utf8mb4",
        },
        autocommit=autocommit,
    )
