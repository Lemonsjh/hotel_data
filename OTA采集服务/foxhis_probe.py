#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Foxhis X5 连接参数自动探测（只读）

用途
  1. 配置页「从本机客户端自动读取」按钮的后端
  2. 手工排查：python foxhis_probe.py [--json] [--deep]

设计
  · 纯标准库，不新增 pip 依赖（可跑在便携 runtime 上）
  · 只读：不写文件、不发非 SELECT 语句
  · 交叉校验：配置文件给出的服务名会与实际接口条目 + TCP 连通性核对，
    避免把「已失效的服务名」回填进配置
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

from foxhis_client_probe import find_foxhis_home, find_sybase_home, find_file, deep_probe

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def read_text_smart(path: Path) -> tuple[str, str]:
    """按 GBK → UTF-8 → latin1 顺序解码（Foxhis 配置文件通常是 GBK）。"""
    raw = path.read_bytes()
    for encoding in ("gbk", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1", errors="replace"), "latin1"


def parse_ini_loose(text: str) -> dict[str, dict[str, str]]:
    """宽松 INI 解析：容忍 `;;` 多重注释、重复小节、键值周围空格。"""
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in ";#":
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if "=" in stripped and current:
            key, value = stripped.split("=", 1)
            sections[current][key.strip()] = value.strip()
    return sections


def tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(deep: bool = False) -> dict:
    """返回 {ok, fields, evidence, warnings}。fields 即配置页可直接回填的字段。"""
    result: dict = {"ok": False, "warnings": [], "fields": {}, "evidence": {}}

    foxhis_home, foxhis_source = find_foxhis_home()
    sybase_home, sybase_source = find_sybase_home()
    result["evidence"]["foxhis_home"] = {
        "path": str(foxhis_home) if foxhis_home else "",
        "source": foxhis_source,
    }
    result["evidence"]["sybase_home"] = {
        "path": str(sybase_home) if sybase_home else "",
        "source": sybase_source,
    }

    if not foxhis_home:
        result["warnings"].append("未找到 Foxhis 客户端安装目录，请手工填写连接参数。")
        return result

    isql = None
    if sybase_home:
        candidate = sybase_home / "BIN" / "isql.exe"
        if candidate.exists():
            isql = candidate
    if not isql:
        isql = find_file("isql.exe", [sybase_home, Path("C:/"), Path("D:/")])
    result["fields"]["isql_path"] = str(isql) if isql else ""
    if not isql:
        result["warnings"].append("未找到 isql.exe，无法走 Open Client 只读通道。")

    interfaces = None
    if sybase_home:
        candidate = sybase_home / "ini" / "SQL.INI"
        if candidate.exists():
            interfaces = candidate
    if not interfaces:
        interfaces = find_file("SQL.INI", [sybase_home, foxhis_home])
    result["fields"]["interfaces_file"] = str(interfaces) if interfaces else ""

    foxhis_ini = foxhis_home / "FOXHIS.INI"
    db_section: dict[str, str] = {}
    if foxhis_ini.exists():
        text, encoding = read_text_smart(foxhis_ini)
        sections = parse_ini_loose(text)
        db_section = sections.get("Database", {})
        result["evidence"]["foxhis_ini"] = {
            "path": str(foxhis_ini),
            "encoding": encoding,
            "sections": sorted(sections),
            "database": db_section,
        }
    else:
        result["warnings"].append(f"{foxhis_ini} 不存在。")

    ini_server = db_section.get("Servername", "")
    result["fields"]["database"] = db_section.get("Database", "")

    entries: dict[str, dict] = {}
    if interfaces and interfaces.exists():
        text, _encoding = read_text_smart(interfaces)
        for name, config in parse_ini_loose(text).items():
            query = config.get("query") or config.get("QUERY") or ""
            parts = [part.strip() for part in query.split(",")]
            if len(parts) >= 3 and parts[2].isdigit():
                host, port = parts[1], int(parts[2])
                entries[name] = {
                    "host": host,
                    "port": port,
                    "protocol": parts[0],
                    "reachable": tcp_reachable(host, port),
                }
        result["evidence"]["sql_ini_entries"] = entries

    if ini_server and ini_server not in entries:
        case_variant = [name for name in entries if name.lower() == ini_server.lower()]
        if case_variant:
            result["warnings"].append(
                f"FOXHIS.INI 写 Servername={ini_server}，但 SQL.INI 里实际是 {case_variant[0]}（大小写不一致）。"
            )
        else:
            result["warnings"].append(
                f"FOXHIS.INI 写 Servername={ini_server}，但 SQL.INI 里没有这个条目 —— 该配置段已失效。"
            )

    dead: list[str] = []
    if foxhis_ini.exists():
        text, _encoding = read_text_smart(foxhis_ini)
        for section, config in parse_ini_loose(text).items():
            server = config.get("Servername", "")
            if server and server not in entries and section != "Database":
                dead.append(f"[{section}] Servername={server}")
    if dead:
        result["warnings"].append(
            "以下配置段引用的服务名在 SQL.INI 里不存在（已失效）：" + "、".join(dead)
        )

    chosen = ""
    if ini_server in entries and entries[ini_server]["reachable"]:
        chosen = ini_server
    elif ini_server:
        variants = [name for name, info in entries.items()
                    if name.lower() == ini_server.lower() and info["reachable"]]
        if len(variants) == 1:
            chosen = variants[0]
    result["fields"]["server"] = chosen
    if chosen and chosen != ini_server:
        result["warnings"].append(f"FOXHIS.INI 指定的服务名不可用，已自动选用可连通的 {chosen}。")
    if not chosen:
        result["warnings"].append("客户端指定的数据库服务不可达或未配置；不会自动切换到其他可达数据库，请人工核对。")

    if chosen:
        info = entries[chosen]
        result["fields"]["server_address"] = f"{info['host']}:{info['port']}"
        result["fields"]["server_host"] = info["host"]
        result["fields"]["server_port"] = info["port"]

    # 字符集：Foxhis 会话为 GBK 环境，推定 cp936，待只读账号实测确认
    result["fields"]["charset"] = "cp936"
    result["evidence"]["charset_reason"] = "GBK 环境推定；待只读账号实测确认"

    if deep and isql and chosen:
        result["evidence"]["deep_check"] = deep_probe(isql, chosen, interfaces)

    result["ok"] = bool(isql and interfaces and chosen and result["fields"].get("database"))
    return result


def render(result: dict) -> str:
    lines = ["Foxhis X5 连接参数自动探测结果", "=" * 62, "", "【可自动回填的字段】"]
    for key, label in (
        ("isql_path", "isql 路径"),
        ("interfaces_file", "接口文件"),
        ("server", "服务名"),
        ("server_address", "服务器地址"),
        ("database", "数据库名"),
        ("charset", "字符集（推定）"),
    ):
        lines.append(f"  {label:<16} {result['fields'].get(key) or '—'}")

    evidence = result.get("evidence", {})
    lines += ["", "【探测依据】"]
    lines.append(f"  客户端目录        {evidence.get('foxhis_home', {}).get('path') or '—'}")
    lines.append(f"  来源              {evidence.get('foxhis_home', {}).get('source') or '—'}")
    lines.append(f"  SYBASE 目录       {evidence.get('sybase_home', {}).get('path') or '—'}")
    ini_meta = evidence.get("foxhis_ini") or {}
    if ini_meta:
        lines.append(f"  FOXHIS.INI 编码   {ini_meta.get('encoding')}")

    entries = evidence.get("sql_ini_entries") or {}
    if entries:
        lines += ["", "【SQL.INI 接口条目与连通性】"]
        for name, info in entries.items():
            lines.append(
                f"  {name:<14} {info['host']}:{info['port']:<6} {'可达' if info['reachable'] else '不可达'}"
            )

    if evidence.get("deep_check"):
        lines += ["", "【深度校验】", f"  {evidence['deep_check']['verdict']}"]

    lines += [
        "",
        "【必须人工填写的字段】",
        "  数据库账号        只读账号（本机不存，无法探测）",
        "  数据库密码        不回显",
        "  采集时机          夜审完成后（业务决策）",
        "  报表清单          房态 / 房费收入（业务决策）",
    ]

    if result["warnings"]:
        lines += ["", "【警告】"] + [f"  · {item}" for item in result["warnings"]]

    lines += ["", f"结论：{'自动探测成功' if result['ok'] else '探测不完整，需人工补齐'}"]
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Foxhis X5 连接参数自动探测（只读）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--deep", action="store_true", help="用无效账号跑 isql，验证服务真实存活")
    args = parser.parse_args()

    result = probe(deep=args.deep)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
