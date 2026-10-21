#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Foxhis 库结构自省工具（只读）

拿到只读账号后的**第一步**：先看清真实库结构，再把 foxhis_sql.py 里的 SQL 定稿。

用法
  python foxhis_introspect.py --version                       # 数据库版本
  python foxhis_introspect.py --list-tables                   # 全部用户表
  python foxhis_introspect.py --search room rm hk             # 按关键字找表
  python foxhis_introspect.py --columns haccount pccode       # 指定表的字段
  python foxhis_introspect.py --sample haccount 5             # 看 5 行样例数据
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from foxhis_common import (  # noqa: E402
    Config,
    FoxhisError,
    check_reachable,
    db_version,
    list_user_tables,
    query,
    table_columns,
)


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    config = Config.from_env()
    if not config.username:
        print("缺少 FOXHIS_USERNAME —— 请先在配置页填入只读账号。", file=sys.stderr)
        return 1

    reachable, detail = check_reachable(config)
    if not reachable:
        print(f"数据库不可达：{detail}", file=sys.stderr)
        return 1

    try:
        if "--version" in args:
            print("数据库版本:", db_version(config) or "读取失败")
            return 0

        if "--list-tables" in args:
            tables = list_user_tables(config)
            print(f"共 {len(tables)} 张用户表：")
            for index in range(0, len(tables), 6):
                print("  " + "  ".join(f"{name:<22}" for name in tables[index:index + 6]))
            return 0

        if "--search" in args:
            keywords = [item.lower() for item in args[args.index("--search") + 1:] if item]
            tables = [name for name in list_user_tables(config)
                      if any(key in name.lower() for key in keywords)]
            print(f"匹配 {len(tables)} 张表：{', '.join(tables) or '无'}")
            for table in tables[:20]:
                columns = table_columns(config, table)
                print(f"\n  [{table}] ({len(columns)} 字段)")
                for index in range(0, len(columns), 5):
                    print("      " + "  ".join(f"{name:<18}" for name in columns[index:index + 5]))
            return 0

        if "--columns" in args:
            for table in args[args.index("--columns") + 1:]:
                columns = table_columns(config, table)
                if not columns:
                    print(f"\n  [{table}] 不存在或不可见")
                    continue
                print(f"\n  [{table}] ({len(columns)} 字段)")
                for index in range(0, len(columns), 5):
                    print("      " + "  ".join(f"{name:<18}" for name in columns[index:index + 5]))
            return 0

        if "--sample" in args:
            position = args.index("--sample")
            table = args[position + 1]
            limit = int(args[position + 2]) if len(args) > position + 2 else 5
            rows = query(f"select top {limit} * from {table}", config, timeout=120)
            for row in rows:
                print("  " + ", ".join(f"{key}={value}" for key, value in row.items()))
            return 0
    except FoxhisError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
