#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""余乐通 E8HotelMis 连接参数探测（只读）。

用途
  1. 配置页「自动识别客户端」「校验数据库连通性」按钮的后端
  2. 手工排查：python yolo_probe.py [--json] [--deep] [--user U --password P]

设计
  · 纯标准库，不新增依赖，可跑在便携 runtime 上
  · 只读：深度校验也只发 SELECT @@VERSION
  · 只依赖 yolo_config：配置页可以直接 import，不必加载采集侧的路径改动
  · 余乐通的账号密码在 数据库设置.ini 里是加密串，探测不回填，必须人工填写
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yolo_config import find_client  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SETTINGS_FILENAME = "数据库设置.ini"
DEFAULT_SECTION = "客房数据库"
DEFAULT_CHARSET = "cp936"


# ---------------------------------------------------------------------------
# 本机客户端与配置文件
# ---------------------------------------------------------------------------


def read_ini(path: Path) -> dict[str, dict[str, str]]:
    """按 GBK 读取余乐通 INI（键值形如 `服务器=127.0.0.1,8829`）。"""
    text = path.read_bytes().decode("gbk", errors="replace")
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in ";#":
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
            sections.setdefault(current, {})
        elif "=" in stripped and current:
            key, value = stripped.split("=", 1)
            sections[current][key.strip()] = value.strip()
    return sections


def find_settings_file(roots: list[Path] | None = None) -> Path | None:
    """在常见安装根目录下按名称找余乐通配置，只下探两层，避免全盘扫描。"""
    override = os.environ.get("YOLO_HOME", "").strip()
    candidates = [Path(override)] if override else []
    if not override:
        project_root = Path(__file__).resolve().parents[1]
        candidates = [project_root, Path("C:/"), Path("D:/")]

    for base in candidates:
        if not base.is_dir():
            continue
        if (base / SETTINGS_FILENAME).is_file():
            return base / SETTINGS_FILENAME
        try:
            entries = [item for item in base.iterdir() if item.is_dir()]
        except OSError:
            continue
        for entry in entries:
            if not _looks_like_yolo(entry.name):
                continue
            if (entry / SETTINGS_FILENAME).is_file():
                return entry / SETTINGS_FILENAME
            try:
                for nested in entry.iterdir():
                    if nested.is_dir() and (nested / SETTINGS_FILENAME).is_file():
                        return nested / SETTINGS_FILENAME
            except OSError:
                continue
    return None


def _looks_like_yolo(name: str) -> bool:
    lowered = name.lower()
    return "yolo" in lowered or "e8hotel" in lowered or "余乐通" in name


def tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def split_endpoint(server: str) -> tuple[str, int]:
    host, _, port = (server or "").partition(",")
    return host.strip(), int(port.strip() or 1433)


# ---------------------------------------------------------------------------
# 深度校验
# ---------------------------------------------------------------------------


def run_select(cli: str, server: str, database: str, username: str,
               password: str, sql: str, charset: str) -> str:
    """单条只读查询；失败时抛 RuntimeError，由调用方转成警告。"""
    command = [
        cli, "-S", server, "-U", username, "-d", database,
        "-y", "8000", "-b", "-l", "15",
        "-Q", f"SET NOCOUNT ON; {sql}",
    ]
    env = os.environ.copy()
    env["SQLCMDPASSWORD"] = password
    env["LANG"] = "enu"
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=30, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("连接超时（30s）") from None
    if completed.returncode:
        detail = completed.stderr.decode(charset, "replace") or completed.stdout.decode(charset, "replace")
        raise RuntimeError(detail.strip()[:300])
    return completed.stdout.decode(charset, "replace")


def single_value(output: str) -> str:
    """取单列结果的有效值：跳过表头与虚线下划线行。"""
    values = [
        line.strip() for line in output.splitlines()
        if line.strip() and not set(line.strip()) <= {"-", "+"}
    ]
    return values[-1] if len(values) > 1 else ""


def deep_check(cli: str, server: str, database: str, username: str,
               password: str, charset: str) -> dict:
    """用只读账号实跑一次查询，证明「能连上」而不只是端口通。"""
    version = single_value(run_select(cli, server, database, username, password,
                                      "select @@VERSION as version", charset))
    if not version:
        raise RuntimeError("连接成功但未取到版本信息，请核对数据库名。")
    rooms = single_value(run_select(cli, server, database, username, password,
                                    "select count(*) as rooms from T_CardRoomInfo", charset))
    return {
        "version": version[:120],
        "room_count": rooms,
        "verdict": "只读账号可用，源库版本与房号表均正常",
    }


# ---------------------------------------------------------------------------
# 探测入口
# ---------------------------------------------------------------------------


def probe(deep: bool = False, username: str = "", password: str = "",
          server: str = "", database: str = "", charset: str = "",
          cli_path: str = "") -> dict:
    """返回 {ok, fields, evidence, warnings}；fields 是可直接回填配置的项。"""
    result: dict = {"ok": False, "fields": {}, "evidence": {}, "warnings": []}
    fields = result["fields"]

    # 配置里已填写的客户端路径（含 osql 兼容适配层）优先，其次才按名称搜索。
    configured_cli = (cli_path or os.environ.get("YOLO_CLI_PATH", "")).strip()
    cli = find_client(configured_cli)
    fields["cli_path"] = cli or ""
    if not cli:
        result["warnings"].append("未找到 SQLCMD/OSQL，请手工填写客户端路径。")

    settings_file = find_settings_file()
    section: dict[str, str] = {}
    if settings_file:
        sections = read_ini(settings_file)
        section = sections.get(DEFAULT_SECTION, {})
        result["evidence"]["settings_file"] = {
            "path": str(settings_file),
            "sections": sorted(sections),
            "host_db": {
                "服务器": section.get("服务器", ""),
                "数据库名": section.get("数据库名", ""),
                "数据来源": section.get("数据来源", ""),
            },
        }
        fields["server"] = section.get("服务器", "")
        fields["database"] = section.get("数据库名", "")
        if section.get("是否使用", "").lower() in {"false", "0", "否"}:
            result["warnings"].append("配置显示客房数据库未启用，请确认这就是在用的业务库。")
    else:
        result["warnings"].append("未找到余乐通的 数据库设置.ini，服务器与库名需人工填写。")

    fields["charset"] = charset or DEFAULT_CHARSET

    # 表单里已填的值优先，探测只做补空
    target_server = server.strip() or fields.get("server", "")
    target_database = database.strip() or fields.get("database", "")
    if not target_server:
        result["warnings"].append("实例地址为空，无法探测连通性。")
        return result

    host, port = split_endpoint(target_server)
    reachable = tcp_reachable(host, port)
    result["evidence"]["endpoint"] = {"host": host, "port": port, "reachable": reachable}
    if not reachable:
        result["warnings"].append(f"{host}:{port} 端口不可达，请确认 SQL Server 已启动。")
        return result

    result["ok"] = bool(cli and target_database)
    if deep:
        if not (username and password):
            result["warnings"].append("深度校验需要填写只读账号与密码。")
        elif not cli:
            result["warnings"].append("缺少命令行客户端，无法做深度校验。")
        else:
            try:
                result["evidence"]["deep_check"] = deep_check(
                    cli, target_server, target_database, username, password,
                    fields["charset"],
                )
            except RuntimeError as exc:
                result["warnings"].append(f"深度校验失败：{exc}")
                result["ok"] = False
    return result


def render(result: dict) -> str:
    lines = ["余乐通 E8HotelMis 连接参数探测结果", "=" * 60, "", "【可自动回填的字段】"]
    for key, label in (("cli_path", "客户端路径"), ("server", "实例地址"),
                       ("database", "数据库名"), ("charset", "字符集")):
        lines.append(f"  {label:<10} {result['fields'].get(key) or '—'}")

    evidence = result.get("evidence") or {}
    settings_meta = evidence.get("settings_file") or {}
    lines += ["", "【探测依据】"]
    lines.append(f"  配置文件    {settings_meta.get('path') or '未找到'}")
    for key, value in (settings_meta.get("host_db") or {}).items():
        lines.append(f"  {key:<10} {value or '—'}")
    endpoint = evidence.get("endpoint") or {}
    if endpoint:
        lines.append(f"  连通性      {endpoint['host']}:{endpoint['port']} "
                     f"{'可达' if endpoint['reachable'] else '不可达'}")
    check = evidence.get("deep_check")
    if check:
        lines += ["", "【深度校验】", f"  源库版本    {check['version']}",
                  f"  房号数      {check['room_count']}", f"  结论        {check['verdict']}"]

    lines += ["", "【必须人工填写】", "  数据库账号  余乐通加密存储，无法探测",
              "  数据库密码  同上，不回显"]
    if result["warnings"]:
        lines += ["", "【警告】"] + [f"  · {item}" for item in result["warnings"]]
    lines += ["", f"结论：{'探测成功' if result['ok'] else '探测不完整，需人工补齐'}"]
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="余乐通连接参数探测（只读）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--deep", action="store_true", help="用只读账号实跑查询")
    parser.add_argument("--user", default="", help="只读账号")
    parser.add_argument("--password", default="", help="只读账号密码")
    parser.add_argument("--server", default="", help="实例地址 host,port")
    parser.add_argument("--database", default="", help="数据库名")
    args = parser.parse_args()

    result = probe(deep=args.deep, username=args.user, password=args.password,
                   server=args.server, database=args.database)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
