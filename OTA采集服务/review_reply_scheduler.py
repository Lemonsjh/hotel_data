from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import runner
from log_maintenance import maintain_logs


ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
STATUS_PATH = STATE_DIR / "review_reply_scheduler_status.json"
PID_PATH = STATE_DIR / "review_reply_scheduler.pid"
STOP_PATH = STATE_DIR / "review_reply_scheduler.stop"
LOG_PATH = ROOT / "logs" / "review_reply_scheduler.log"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def config(settings: dict[str, Any]) -> dict[str, Any]:
    return settings.get("reply_scheduler") or {}


def interval_minutes(settings: dict[str, Any]) -> int:
    return max(1, int((settings.get("price_scheduler") or {}).get("interval_minutes", 5)))


def save_status(**changes: Any) -> None:
    data = runner.load_json(STATUS_PATH, {})
    data.update(changes)
    runner.save_json(STATUS_PATH, data)


def claim_process() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(PID_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("review reply scheduler already has a PID file") from exc
    with os.fdopen(descriptor, "w", encoding="ascii") as file:
        file.write(str(os.getpid()))
    return os.getpid()


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


def run_executor(settings: dict[str, Any], max_tasks: int) -> int:
    script = runner.script_path(settings, "meituan", "meituan_review_reply_task.py")
    if not script.exists():
        raise FileNotFoundError(f"Reply executor not found: {script}")
    maintain_logs(LOG_PATH.parent, LOG_PATH)
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n[{now_text()}] pending review reply scan started\n")
        log.flush()
        completed = subprocess.run(
            [str(runner.python_path(settings)), str(script), "--max-tasks", str(max_tasks)],
            cwd=str(ROOT),
            env=runner.build_env(settings, "meituan"),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log.write(f"[{now_text()}] scan finished, return_code={completed.returncode}\n")
    return completed.returncode


def run_scheduler() -> int:
    settings = runner.load_settings()
    pid = claim_process()
    STOP_PATH.unlink(missing_ok=True)
    save_status(
        scheduler_status="waiting", pid=pid, started_at=now_text(), next_run_at=now_text(),
        last_run_finished_at="", last_run_return_code=None, message="waiting for pending review replies",
    )
    try:
        while not STOP_PATH.exists():
            settings = runner.load_settings()
            cfg = config(settings)
            if not cfg.get("enabled", False):
                break
            save_status(scheduler_status="executing", next_run_at="", message="executing pending review replies")
            return_code = run_executor(settings, max(1, int(cfg.get("max_tasks_per_run", 10))))
            next_run = datetime.now() + timedelta(minutes=interval_minutes(settings))
            save_status(
                scheduler_status="waiting", last_run_finished_at=now_text(), last_run_return_code=return_code,
                next_run_at=next_run.strftime("%Y-%m-%d %H:%M:%S"), message="waiting for pending review replies",
            )
            if wait_until(next_run):
                break
        return 0
    finally:
        save_status(scheduler_status="stopped", pid=None, next_run_at="", message="review reply scheduler stopped")
        STOP_PATH.unlink(missing_ok=True)
        release_process(pid)


if __name__ == "__main__":
    try:
        raise SystemExit(run_scheduler())
    except Exception as exc:
        if not PID_PATH.exists():
            save_status(scheduler_status="failed", pid=None, next_run_at="", message=str(exc))
        print(f"review reply scheduler failed: {exc}", file=sys.stderr)
        raise
