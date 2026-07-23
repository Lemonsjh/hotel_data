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
RUNNER_PATH = ROOT / "runner.py"
STATE_DIR = ROOT / "state"
STATUS_PATH = STATE_DIR / "manual_scheduler_status.json"
PID_PATH = STATE_DIR / "manual_scheduler.pid"
STOP_PATH = STATE_DIR / "manual_scheduler.stop"
LOG_PATH = ROOT / "logs" / "manual_scheduler.log"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_settings() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def interval_minutes() -> int:
    value = (load_settings().get("service") or {}).get("interval_minutes", 30)
    return max(1, int(value))


def save_status(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if STATUS_PATH.exists():
        try:
            data = json.loads(STATUS_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data.update(changes)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = STATUS_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(STATUS_PATH)
    return data


def claim_process() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(PID_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("manual scheduler already has a PID file")
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


def run_collection() -> int:
    maintain_logs(LOG_PATH.parent, LOG_PATH)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n[{now_text()}] full collection started\n")
        log.flush()
        completed = subprocess.run(
            [sys.executable, str(RUNNER_PATH), "run-once"],
            cwd=str(ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log.write(f"[{now_text()}] full collection finished, return_code={completed.returncode}\n")
    return completed.returncode


def wait_for_next_run(target: datetime) -> bool:
    while datetime.now() < target:
        if STOP_PATH.exists():
            return True
        time.sleep(min(2, max(0.1, (target - datetime.now()).total_seconds())))
    return STOP_PATH.exists()


def run_scheduler() -> int:
    if not CONFIG_PATH.exists() or not RUNNER_PATH.exists():
        raise FileNotFoundError("settings.json or runner.py is missing")

    pid = claim_process()
    STOP_PATH.unlink(missing_ok=True)
    started_at = now_text()
    save_status(
        scheduler_status="running",
        pid=pid,
        started_at=started_at,
        current_run_started_at="",
        last_run_finished_at="",
        last_run_return_code=None,
        next_run_at="",
        stop_requested=False,
        message="manual scheduler started",
    )

    try:
        while not STOP_PATH.exists():
            run_started = datetime.now()
            save_status(
                scheduler_status="collecting",
                current_run_started_at=now_text(),
                next_run_at="",
                stop_requested=False,
                message="full collection is running",
            )
            return_code = run_collection()
            finished_at = now_text()
            save_status(
                current_run_started_at="",
                last_run_finished_at=finished_at,
                last_run_return_code=return_code,
            )
            if STOP_PATH.exists():
                break

            # Keep a stable cadence from the start of each collection run.
            next_run = run_started + timedelta(minutes=interval_minutes())
            save_status(
                scheduler_status="waiting",
                next_run_at=next_run.strftime("%Y-%m-%d %H:%M:%S"),
                message="waiting for next collection",
            )
            if wait_for_next_run(next_run):
                break
        return 0
    finally:
        save_status(
            scheduler_status="stopped",
            pid=None,
            current_run_started_at="",
            next_run_at="",
            stop_requested=False,
            message="manual scheduler stopped",
        )
        STOP_PATH.unlink(missing_ok=True)
        release_process(pid)


if __name__ == "__main__":
    try:
        raise SystemExit(run_scheduler())
    except Exception as exc:
        if not PID_PATH.exists():
            save_status(scheduler_status="failed", pid=None, next_run_at="", message=str(exc))
        print(f"manual scheduler failed: {exc}", file=sys.stderr)
        raise
