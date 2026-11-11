"""从已经打开的 Rezen One 读取当前酒店会话。

慧云在自己的宿主进程里反射 WorkItem 状态中的 CookieContainer。
Rezen One 不允许再开第二个客户端，所以这里只附加到已经登录的进程，
读取同一条登录缓存里当前酒店的 Cookie。标准输出只有会话本身，调用方不得记录。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "ProcessSession.cs"
LIBRARY = ROOT / "lib" / "Microsoft.Diagnostics.Runtime.dll"
RUNTIME = Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "HotelAgent" / "rezen"


def read_process_session() -> str:
    if os.name != "nt":
        raise RuntimeError("Rezen 进程会话仅支持 Windows")
    exe = _compile()
    try:
        result = subprocess.run(
            [str(exe)], check=False, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError("读取 Rezen 登录会话超时") from None
    try:
        token = result.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        raise RuntimeError("没有找到已登录的 Rezen 酒店会话，请先打开并登录 Rezen One") from None
    if result.returncode != 0 or not token or any(char in token for char in "\r\n\t ;"):
        raise RuntimeError("没有找到已登录的 Rezen 酒店会话，请先打开并登录 Rezen One")
    return token


def _compile() -> Path:
    compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    if not compiler.is_file() or not LIBRARY.is_file() or not SOURCE.is_file():
        raise RuntimeError("缺少读取 Rezen 登录会话的组件")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(SOURCE.read_bytes() + LIBRARY.read_bytes()).hexdigest()
    marker = RUNTIME / "process_session.sha256"
    exe = RUNTIME / "RezenProcessSession.exe"
    library = RUNTIME / LIBRARY.name
    if exe.is_file() and library.is_file() and marker.is_file() and marker.read_text(encoding="utf-8") == digest:
        return exe
    library.write_bytes(LIBRARY.read_bytes())
    completed = subprocess.run(
        [str(compiler), "/nologo", "/platform:x86", f"/reference:{library}", f"/out:{exe}", str(SOURCE)],
        check=False, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode != 0 or not exe.is_file():
        raise RuntimeError("无法准备 Rezen 登录会话读取")
    marker.write_text(digest, encoding="utf-8")
    return exe
