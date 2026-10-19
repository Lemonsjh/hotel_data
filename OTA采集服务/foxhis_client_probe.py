"""本机西软/Sybase 客户端定位与显式连通性探测。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
from pathlib import Path


def running_process_paths(exe_name: str = "front.exe") -> list[str]:
    """枚举进程，找指定 exe 的完整路径（纯标准库，不依赖 psutil）。"""
    if os.name != "nt":
        return []
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi

    count = 2048
    pids = (wt.DWORD * count)()
    needed = wt.DWORD()
    if not psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(needed)):
        return []

    found: list[str] = []
    total = min(needed.value // ctypes.sizeof(wt.DWORD), count)
    for index in range(total):
        pid = pids[index]
        if not pid:
            continue
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(len(buffer))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                path = buffer.value
                if path.lower().endswith(exe_name.lower()):
                    found.append(path)
        finally:
            kernel32.CloseHandle(handle)
    return found


def find_foxhis_home() -> tuple[Path | None, str]:
    candidates: list[tuple[Path, str]] = []

    env = os.environ.get("FOXHIS_HOME")
    if env:
        candidates.append((Path(env), "环境变量 FOXHIS_HOME"))

    for path in running_process_paths("front.exe"):
        candidates.append((Path(path).parent, "运行中的 front.exe 进程路径"))

    for root in (Path("C:/"), Path("D:/")):
        if not root.exists():
            continue
        try:
            for item in root.iterdir():
                if item.is_dir() and "foxhis" in item.name.lower():
                    candidates.append((item, "磁盘目录名匹配 *foxhis*"))
        except OSError:
            continue

    for path, source in candidates:
        if (path / "front.exe").exists():
            return path, source
    for path, source in candidates:
        if (path / "FOXHIS.INI").exists():
            return path, source + "（仅有 FOXHIS.INI）"
    return None, "未找到"


def find_sybase_home() -> tuple[Path | None, str]:
    env = os.environ.get("SYBASE")
    if env and Path(env).is_dir():
        return Path(env), "环境变量 SYBASE"
    for candidate in (Path("C:/SYBASE"), Path("D:/SYBASE")):
        if candidate.is_dir():
            return candidate, "磁盘目录探测"
    return None, "未找到"


def find_file(name: str, roots: list[Path | None], max_depth: int = 3) -> Path | None:
    for root in roots:
        if not root or not root.is_dir():
            continue
        direct = root / name
        if direct.exists():
            return direct
        for level in range(1, max_depth + 1):
            pattern = "/".join(["*"] * level) + f"/{name}"
            try:
                hit = next(iter(root.glob(pattern)), None)
            except OSError:
                hit = None
            if hit:
                return hit
    return None


def deep_probe(isql: Path, server: str, interfaces: Path | None) -> dict:
    """
    用无效探测账号跑一次 isql。
    预期：不存在的服务名 → "Requested server name not found"；真实服务 → 服务端 "Login failed"。
    后者证明链路完整、服务在线（不需要真实凭据）。
    """
    env = os.environ.copy()
    env["SYBASE"] = str(isql.parent.parent)
    env["LANG"] = "enu"
    env.pop("LC_ALL", None)

    def run(server_name: str) -> str:
        command = [str(isql), "-S", server_name, "-U", "probe_nouser", "-P", "probe_nopwd"]
        if interfaces:
            command += ["-I", str(interfaces)]
        try:
            completed = subprocess.run(
                command,
                input="quit\n",
                capture_output=True,
                text=True,
                timeout=25,
                env=env,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return (completed.stdout or "") + (completed.stderr or "")
        except subprocess.TimeoutExpired:
            return "<timeout>"
        except OSError as exc:
            return f"<error: {exc}>"

    real = run(server)
    fake = run("NoSuchServer_ZZZ")
    real_alive = "login failed" in real.lower() or "password" in real.lower()
    fake_missing = "not found" in fake.lower() or "unable to connect" in fake.lower()
    return {
        "real_server_output": real.strip()[:400],
        "fake_server_output": fake.strip()[:400],
        "verdict": (
            "服务真实在线（服务端返回 Login failed，说明链路通、仅探测账号无效）"
            if real_alive and fake_missing
            else "结果不典型，需人工判断"
        ),
    }
