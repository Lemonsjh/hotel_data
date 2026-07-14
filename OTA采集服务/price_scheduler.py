from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from log_maintenance import maintain_logs


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "settings.json"
EXECUTOR_PATH = ROOT / "price_executor.py"
STATE_DIR = ROOT / "state"
STATUS_PATH = STATE_DIR / "price_scheduler_status.json"
PID_PATH = STATE_DIR / "price_scheduler.pid"
STOP_PATH = STATE_DIR / "price_scheduler.stop"
LOG_PATH = ROOT / "logs" / "price_scheduler.log"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def settings() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def config() -> dict[str, Any]:
    return settings().get("price_scheduler") or {}


def save_status(**changes: Any) -> None:
    data: dict[str, Any] = {}
    if STATUS_PATH.exists():
        try:
            data = json.loads(STATUS_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            pass
    data.update(changes)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATUS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATUS_PATH)


def claim_process() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(PID_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("price scheduler already has a PID file") from exc
    pid = os.getpid()
    with os.fdopen(descriptor, "w", encoding="ascii") as file:
        file.write(str(pid))
    return pid


def release_process(pid: int) -> None:
    try:
        if PID_PATH.read_text(encoding="ascii").strip() == str(pid):
            PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def wait_until(target: datetime) -> bool:
    while datetime.now() < target:
        if STOP_PATH.exists():
            return True
        time.sleep(min(2, max(0.1, (target - datetime.now()).total_seconds())))
    return STOP_PATH.exists()


def run_executor(max_tasks: int) -> int:
    maintain_logs(LOG_PATH.parent, LOG_PATH)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n[{now_text()}] pending price task scan started\n")
        log.flush()
        completed = subprocess.run(
            [sys.executable, str(EXECUTOR_PATH), "--platform", "all", "--max-tasks", str(max_tasks)],
            cwd=str(ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log.write(f"[{now_text()}] scan finished, return_code={completed.returncode}\n")
    return completed.returncode


def run_scheduler() -> int:
    if not CONFIG_PATH.exists() or not EXECUTOR_PATH.exists():
        raise FileNotFoundError("settings.json or price_executor.py is missing")
    pid = claim_process()
    STOP_PATH.unlink(missing_ok=True)
    cfg = config()
    delay = max(0, int(cfg.get("startup_delay_seconds", 60)))
    first_run = datetime.now() + timedelta(seconds=delay)
    save_status(
        scheduler_status="waiting",
        pid=pid,
        started_at=now_text(),
        next_run_at=first_run.strftime("%Y-%m-%d %H:%M:%S"),
        last_run_finished_at="",
        last_run_return_code=None,
        message="waiting for first pending-task scan",
    )
    try:
        if wait_until(first_run):
            return 0
        while not STOP_PATH.exists():
            cfg = config()
            if not cfg.get("enabled", True):
                break
            save_status(scheduler_status="executing", next_run_at="", message="executing pending price tasks")
            return_code = run_executor(max(1, int(cfg.get("max_tasks_per_run", 20))))
            interval = max(1, int(cfg.get("interval_minutes", 5)))
            next_run = datetime.now() + timedelta(minutes=interval)
            save_status(
                scheduler_status="waiting",
                last_run_finished_at=now_text(),
                last_run_return_code=return_code,
                next_run_at=next_run.strftime("%Y-%m-%d %H:%M:%S"),
                message="waiting for next pending-task scan",
            )
            if wait_until(next_run):
                break
        return 0
    finally:
        save_status(scheduler_status="stopped", pid=None, next_run_at="", message="price scheduler stopped")
        STOP_PATH.unlink(missing_ok=True)
        release_process(pid)


if __name__ == "__main__":
    try:
        raise SystemExit(run_scheduler())
    except Exception as exc:
        if not PID_PATH.exists():
            save_status(scheduler_status="failed", pid=None, next_run_at="", message=str(exc))
        print(f"price scheduler failed: {exc}", file=sys.stderr)
        raise
