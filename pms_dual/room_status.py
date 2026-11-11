"""美团 PMS 与 Rezen 的当天房态：两侧都成功才按统一房型合并写入一次。"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / "OTA采集服务")]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from meituan_pms.collector import collect as collect_meituan
from pms_dual.merge import SOURCE, aliases_from_rows, merge_rows
from pms_dual.state import remember
from rezen_pms.collector import _tax, _view_days, collect as collect_rezen
from rezen_pms.storage import sync_mysql


def now_shanghai():
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def hotel_name(meituan_name: str, rezen_name: str) -> str:
    left, right = meituan_name.strip(), rezen_name.strip()
    if left and right and left != right:
        raise ValueError("美团 PMS 与 Rezen 的酒店名称不一致，本轮不写入")
    if not (left or right):
        raise ValueError("请配置酒店名称")
    return left or right


def load_aliases(hotel_id: str) -> dict:
    from ota_mysql_writer import connect_mysql
    connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT source_platform, source_room_type_name, room_type_id "
                        "FROM hotel_room_type_mapping WHERE hotel_id=%s AND is_active=1 "
                        "AND source_product_id='' AND source_platform IN ('pms_meituan','pms_rezen')",
                        (hotel_id,))
            rows = []
            for row in cur.fetchall():
                if isinstance(row, dict):
                    rows.append((row["source_platform"], row["source_room_type_name"], row["room_type_id"]))
                else:
                    rows.append(row)
        return aliases_from_rows(rows)
    finally:
        connection.close()


def run(clock=now_shanghai, aliases=None) -> dict[str, list]:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    name = hotel_name(os.environ.get("MEITUAN_PMS_HOTEL_NAME", ""), os.environ.get("REZEN_HOTEL_NAME", ""))
    if not hotel_id:
        raise ValueError("请配置酒店 ID")
    meituan = collect_meituan(os.environ.get("MEITUAN_PMS_COOKIE", ""), hotel_id, name, clock=clock)
    remember("meituan")
    rezen = collect_rezen(
        os.environ.get("REZEN_SESSION", ""), hotel_id, name, clock=clock,
        base_url=os.environ.get("REZEN_BASE_URL", ""),
        profile_dir=os.environ.get("REZEN_PROFILE_DIR", "").strip(),
        view_days=_view_days(os.environ.get("REZEN_VIEW_DAYS", "")),
        include_tax=_tax(os.environ.get("REZEN_INCLUDE_TAX", "")))
    remember("rezen")
    try:
        mapping = load_aliases(hotel_id) if aliases is None else aliases
    except ValueError:
        raise
    except Exception:
        raise RuntimeError("读取房型映射失败，本轮不写入") from None
    snapshot = meituan[0]["snapshot_time"]
    merged = merge_rows(meituan, rezen, mapping, hotel_id=hotel_id, hotel_name=name,
                        snapshot=snapshot, left_platform="pms_meituan", right_platform="pms_rezen")
    return {"forecast": merged, "hourly": merged}


def main(argv=None):
    parser = argparse.ArgumentParser(description="美团 PMS + Rezen 房态汇总")
    parser.add_argument("--dry-run", action="store_true", help="只查询并合并，不写入 MySQL")
    args = parser.parse_args(argv)
    try:
        rows = run()
        if not args.dry_run:
            sync_mysql(rows["forecast"], rows["hourly"], source=SOURCE)
        print(f"美团+Rezen 房态{'校验' if args.dry_run else '入库'}完成："
              f"预测 {len(rows['forecast'])} 行，小时 {len(rows['hourly'])} 行")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
