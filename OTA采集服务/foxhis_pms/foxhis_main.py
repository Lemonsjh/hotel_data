#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Foxhis X5 采集统一入口

用途：手工一次跑完全部报表（便于调试）。
采集服务（runner.py）在 pms_fetch 任务里是按报表逐个启动脚本的，不经过本文件。

用法
  python foxhis_main.py                # 跑全部报表
  python foxhis_main.py --dry-run      # 全部只取数不落库
  python foxhis_main.py --only room_revenue
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import foxhis_room_revenue_data  # noqa: E402
import foxhis_room_status_data  # noqa: E402
import foxhis_jl01_data  # noqa: E402
import foxhis_jy01_data  # noqa: E402
import foxhis_jy03_data  # noqa: E402
import foxhis_jl11_data  # noqa: E402
import foxhis_jd01_data  # noqa: E402

REPORTS = {
    "room_status": foxhis_room_status_data.main,
    "room_revenue": foxhis_room_revenue_data.main,
    "jl01": foxhis_jl01_data.main,
    "jy01": foxhis_jy01_data.main,
    "jy03": foxhis_jy03_data.main,
    "jl11": foxhis_jl11_data.main,
    "jd01": foxhis_jd01_data.main,
}


def main() -> int:
    args = sys.argv[1:]
    selected = list(REPORTS)
    if "--only" in args:
        key = args[args.index("--only") + 1]
        if key not in REPORTS:
            print(f"未知报表 {key}；可选：{', '.join(REPORTS)}", file=sys.stderr)
            return 1
        selected = [key]

    failed: list[str] = []
    for key in selected:
        print(f"\n=== Foxhis 采集：{key} ===")
        try:
            if REPORTS[key]() != 0:
                failed.append(key)
        except Exception as exc:  # noqa: BLE001 — 单张报表失败不应阻断其他报表
            print(f"FAIL [{key}] {type(exc).__name__}: {exc}", file=sys.stderr)
            failed.append(key)

    if failed:
        print(f"\n完成，但有 {len(failed)} 张报表失败：{', '.join(failed)}", file=sys.stderr)
        return 1
    print("\n全部报表采集完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
