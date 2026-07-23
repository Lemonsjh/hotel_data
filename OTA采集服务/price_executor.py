from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import price_tasks
import runner
from data_retention import cleanup_price_tasks


LOCK_NAME = "hotel_ota_price_executor"
TASK_TIMEOUT_SECONDS = 600


def log(message: str, log_path: Path) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    with log_path.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def sanitize(text: str) -> str:
    return re.sub(r"(mtgsig=)[^&\s]+", r"\1***", text or "")


def acquire_lock(settings: dict[str, Any]):
    conn = price_tasks.connection(settings)
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, 0) AS acquired", (LOCK_NAME,))
        row = cur.fetchone()
    if not row or int(row["acquired"] or 0) != 1:
        conn.close()
        return None
    return conn


def release_lock(conn) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
    finally:
        conn.close()


def claim_next(settings: dict[str, Any], platform: str, conn=None) -> dict[str, Any] | None:
    info = price_tasks.require_platform(platform)
    owns_connection = conn is None
    conn = conn or price_tasks.connection(settings)
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, hotel_name, ota_product_id, business_date, target_sale_price
                FROM {info['tasks']}
                WHERE execute_status='PENDING'
                  AND business_date >= CURRENT_DATE
                ORDER BY created_at, id
                LIMIT 1
                FOR UPDATE
                """
            )
            task = cur.fetchone()
            if not task:
                conn.rollback()
                return None
            cur.execute(
                f"UPDATE {info['tasks']} SET execute_status='EXECUTING' WHERE id=%s AND execute_status='PENDING'",
                (task["id"],),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return None
        conn.commit()
        return task
    finally:
        if owns_connection:
            conn.close()


def task_status(settings: dict[str, Any], platform: str, task_id: int) -> str:
    info = price_tasks.require_platform(platform)
    with price_tasks.connection(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT execute_status FROM {info['tasks']} WHERE id=%s", (task_id,))
        row = cur.fetchone()
    return str((row or {}).get("execute_status") or "")


def task_command(settings: dict[str, Any], platform: str, task: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    info = price_tasks.require_platform(platform)
    script = price_tasks.PRICE_DIR / info["script"]
    if not script.exists():
        raise FileNotFoundError(f"调价脚本不存在：{script}")
    if platform == "meituan":
        price_tasks.sync_meituan_hotel_config(settings, task["hotel_name"])

    command = [
        str(runner.python_path(settings)),
        str(script),
        "--task-id",
        str(task["id"]),
        "--hotel-name",
        str(task["hotel_name"]),
        "--headless",
        "--check-seconds",
        "10",
    ]
    env = runner.build_env(settings, platform)
    env["PYTHONIOENCODING"] = "utf-8"
    browser_dir = runner.PROJECT_ROOT / "runtime" / "playwright-browsers"
    if browser_dir.exists():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)
    if platform == "meituan":
        env["MEITUAN_COOKIE"] = str((settings.get("meituan") or {}).get("me_cookie") or "")
        cookie_key = "MEITUAN_COOKIE"
    else:
        env["CTRIP_COOKIE"] = str((settings.get("ctrip") or {}).get("cookie") or "")
        cookie_key = "CTRIP_COOKIE"
    if not env[cookie_key]:
        raise ValueError(f"{info['label']} Cookie 为空")
    return command, env


def execute_task(settings: dict[str, Any], platform: str, task: dict[str, Any], log_path: Path) -> bool:
    task_id = int(task["id"])
    info = price_tasks.require_platform(platform)
    log(f"开始执行 {info['label']} task_id={task_id}", log_path)
    try:
        command, env = task_command(settings, platform, task)
        completed = subprocess.run(
            command,
            cwd=str(price_tasks.PRICE_DIR),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TASK_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = sanitize((completed.stdout or "") + (completed.stderr or ""))
        if output.strip():
            with log_path.open("a", encoding="utf-8") as file:
                file.write(output.rstrip() + "\n")
        status = task_status(settings, platform, task_id)
        ok = completed.returncode == 0 and status == "SUCCESS"
        if not ok and status == "EXECUTING":
            price_tasks.set_task_status(settings, platform, task_id, "FAILED")
        log(f"完成 task_id={task_id} return_code={completed.returncode} status={task_status(settings, platform, task_id)}", log_path)
        return ok
    except Exception as exc:
        price_tasks.set_task_status(settings, platform, task_id, "FAILED")
        log(f"失败 task_id={task_id} error={exc}", log_path)
        return False


def run_once(platforms: list[str], max_tasks: int) -> int:
    settings = runner.load_settings()
    price_tasks.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = price_tasks.LOG_DIR / f"{datetime.now():%Y%m%d}_price_executor.log"
    lock_conn = acquire_lock(settings)
    if lock_conn is None:
        log("已有调价执行器运行，本次退出", log_path)
        return 0

    processed = 0
    failed = 0
    try:
        try:
            cleanup_price_tasks(lock_conn, settings, lambda message: log(message, log_path))
        except Exception as exc:
            log(f"Price-task retention cleanup failed: {exc}", log_path)
        for platform in platforms:
            while processed < max_tasks:
                task = claim_next(settings, platform, lock_conn)
                if not task:
                    break
                processed += 1
                if not execute_task(settings, platform, task, log_path):
                    failed += 1
        if processed == 0:
            log("没有可执行的 PENDING 调价任务，未启动浏览器", log_path)
        else:
            log(f"本次处理完成 processed={processed} failed={failed}", log_path)
    finally:
        release_lock(lock_conn)
    return 2 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OTA PENDING 调价任务定时执行器")
    parser.add_argument("--platform", choices=["all", "meituan", "ctrip"], default="all")
    parser.add_argument("--max-tasks", type=int, default=20)
    args = parser.parse_args()
    platforms = list(price_tasks.PLATFORMS) if args.platform == "all" else [args.platform]
    return run_once(platforms, max(1, args.max_tasks))


if __name__ == "__main__":
    raise SystemExit(main())
