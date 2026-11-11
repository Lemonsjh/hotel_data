"""从已经登录的 Rezen One 读取会话。磁盘 Cookie 没有酒店会话时，读取当前打开的客户端进程。不记录会话内容。"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sqlite3
import subprocess
import tempfile
from ctypes import wintypes
from pathlib import Path

from rezen_pms.crypto import aes_gcm_decrypt


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(blob: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(blob, len(blob))
    incoming = _Blob(len(blob), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
    outgoing = _Blob()
    if not crypt32.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 0, ctypes.byref(outgoing)):
        raise RuntimeError("Rezen 会话无法在本机解开，请在配置中填写会话")
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        kernel32.LocalFree(outgoing.pbData)


def _read_shared(path: Path) -> bytes:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileSize.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetFileSize.restype = wintypes.DWORD
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(str(path), 0x80000000, 7, None, 3, 0, None)
    if handle is None or handle == wintypes.HANDLE(-1).value:
        raise RuntimeError("Rezen 会话文件暂时无法读取")
    try:
        size = kernel32.GetFileSize(handle, None)
        if size in {0, 0xFFFFFFFF}:
            raise RuntimeError("Rezen 会话文件暂时无法读取")
        buffer = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        if not kernel32.ReadFile(handle, buffer, size, ctypes.byref(read), None) or read.value != size:
            raise RuntimeError("Rezen 会话文件暂时无法读取")
        return buffer.raw
    finally:
        kernel32.CloseHandle(handle)


def _accept(value: str) -> str:
    text = value.strip()
    if len(text) < 8 or any(char in text for char in "\r\n"):
        raise ValueError("Rezen 会话无效，请重新登录客户端")
    return text


def decrypt_cookie_value(encrypted: bytes, key: bytes) -> str:
    if encrypted[:3] not in {b"v10", b"v11"} or len(encrypted) < 31:
        raise ValueError("当前 Rezen 会话加密方式无法在进程外读取，请在配置中填写会话")
    return aes_gcm_decrypt(key, encrypted[3:15], encrypted[15:-16], encrypted[-16:]).decode("utf-8")


def encryption_key(prefs_path: Path) -> bytes:
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        raw = base64.b64decode(prefs["os_crypt"]["encrypted_key"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        raise RuntimeError("Rezen 会话无法在本机解开，请在配置中填写会话") from None
    if not raw.startswith(b"DPAPI"):
        raise RuntimeError("Rezen 会话无法在本机解开，请在配置中填写会话")
    return _dpapi(raw[5:])


def _profiles(root: Path | None = None) -> list[Path]:
    base = root or Path(os.environ.get("TEMP") or os.environ.get("TMP") or "")
    found = [
        child for child in base.iterdir()
        if child.is_dir() and child.name.startswith("RezenOne-")
        and (child / "Network" / "Cookies").is_file() and (child / "LocalPrefs.json").is_file()
    ] if base.is_dir() else []
    return sorted(found, key=lambda item: (item / "Network" / "Cookies").stat().st_mtime, reverse=True)


def discover_profile(root: Path | None = None) -> Path:
    found = _profiles(root)
    if not found:
        raise ValueError("没有找到已登录的 Rezen 会话，请先打开并登录 Rezen One")
    return found[0]


def load_session(profile_dir: Path | None = None, root: Path | None = None) -> str:
    return load_auth(profile_dir, root)[0]


def load_auth(profile_dir: Path | None = None, root: Path | None = None, reader=None) -> tuple[str, str]:
    profiles = [Path(profile_dir)] if profile_dir else _profiles(root)
    missing = None
    for profile in profiles:
        try:
            return _load_profile(profile)
        except ValueError as exc:
            if profile_dir or "没有找到" not in str(exc):
                raise
            missing = exc
        except RuntimeError:
            if profile_dir:
                raise
    if profile_dir is not None:
        raise missing or ValueError("没有找到已登录的 Rezen 会话，请先打开并登录 Rezen One")
    return _from_process(reader)


def _from_process(reader) -> tuple[str, str]:
    try:
        if reader is None:
            from rezen_pms.process_session import read_process_session
            reader = read_process_session
        token = reader()
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        raise ValueError("没有找到已登录的 Rezen 酒店会话，请先打开并登录 Rezen One") from None
    return _accept(token), ""


def _load_profile(profile: Path) -> tuple[str, str]:
    cookies = profile / "Network" / "Cookies"
    prefs = profile / "LocalPrefs.json"
    if not cookies.is_file() or not prefs.is_file():
        raise ValueError("没有找到已登录的 Rezen 会话，请先打开并登录 Rezen One")
    with tempfile.TemporaryDirectory() as folder:
        database = _copy_cookie_db(cookies, Path(folder))
        connection = sqlite3.connect(database)
        try:
            rows = connection.execute(
                "SELECT host_key, name, value, encrypted_value FROM cookies "
                "ORDER BY last_access_utc DESC").fetchall()
        finally:
            connection.close()
    rows = sorted(rows, key=lambda item: 0 if "rezen.tech" in str(item[0] or "") else 1)
    key = None
    found: dict[str, str] = {}
    for _host, name, value, encrypted in rows:
        cookie_name = str(name or "").strip()
        if not cookie_name or cookie_name in found:
            continue
        text = str(value or "").strip()
        if not text:
            blob = bytes(encrypted or b"")
            if not blob:
                continue
            if key is None:
                key = encryption_key(prefs)
            try:
                text = decrypt_cookie_value(blob, key).strip()
            except ValueError:
                if cookie_name in {"rezenid", "rezensid", "sid"}:
                    raise
                continue
        if not text or any(char in text for char in "\r\n;"):
            continue
        found[cookie_name] = text
    session = found.get("rezenid") or found.get("rezensid")
    if not session:
        raise ValueError("没有找到已登录的 Rezen 酒店会话，请在配置中填写会话")
    cookie = "; ".join(f"{name}={value}" for name, value in found.items())
    return _accept(session), cookie


def _copy_cookie_db(cookies: Path, directory: Path) -> Path:
    target = directory / "Cookies"
    target.write_bytes(_read_shared(cookies))
    for suffix in ("-wal", "-journal"):
        extra = Path(str(cookies) + suffix)
        if not extra.is_file():
            continue
        try:
            (directory / f"Cookies{suffix}").write_bytes(_read_shared(extra))
        except RuntimeError:
            continue
    return target
