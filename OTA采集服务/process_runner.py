from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ProcessResult:
    return_code: int
    output_tail: str


class ProcessTimeoutError(TimeoutError):
    def __init__(self, timeout: int, output_tail: str):
        super().__init__(f"timeout after {timeout}s")
        self.timeout = timeout
        self.output_tail = output_tail


def run_streamed(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    log_path: Path,
    transform: Callable[[str], str],
) -> ProcessResult:
    """运行子进程，将合并输出逐行写入日志，并仅保留有限尾部。"""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                output_queue.put(line)
        finally:
            process.stdout.close()
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    tail: deque[str] = deque(maxlen=500)
    stream_closed = False

    with log_path.open("w", encoding="utf-8") as log:
        while not stream_closed or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                reader.join(timeout=2)
                while not output_queue.empty():
                    item = output_queue.get_nowait()
                    if item is not None:
                        clean = transform(item)
                        log.write(clean)
                        tail.append(clean)
                log.flush()
                raise ProcessTimeoutError(timeout, "".join(tail))
            try:
                item = output_queue.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if item is None:
                stream_closed = True
                continue
            clean = transform(item)
            log.write(clean)
            log.flush()
            tail.append(clean)

    return ProcessResult(process.wait(), "".join(tail))
