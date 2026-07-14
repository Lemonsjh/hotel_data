from __future__ import annotations

import time
from pathlib import Path


RETENTION_DAYS = 30
MAX_LOG_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3


def maintain_logs(log_dir: Path, active_log: Path) -> None:
    log_dir = log_dir.resolve()
    active_log = active_log.resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    if active_log.parent != log_dir:
        raise ValueError("active log must be inside the log directory")
    cleanup_old_logs(log_dir, active_log)
    rotate_log(active_log)


def cleanup_old_logs(log_dir: Path, active_log: Path) -> None:
    cutoff = time.time() - RETENTION_DAYS * 86400
    for path in log_dir.glob("*.log*"):
        try:
            if path.resolve() != active_log and path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def rotate_log(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size < MAX_LOG_BYTES:
            return
        oldest = path.with_name(f"{path.name}.{BACKUP_COUNT}")
        oldest.unlink(missing_ok=True)
        for index in range(BACKUP_COUNT - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                source.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        pass
