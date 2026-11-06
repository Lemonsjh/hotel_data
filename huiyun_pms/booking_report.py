"""慧云 JD01：首次最近30天，后续当天，保留90天。"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from huiyun_pms.booking_storage import sync_mysql
from huiyun_pms.booking_transform import transform
from huiyun_pms.booking_window import collection_window
from huiyun_pms.bridge import request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='只查询和校验，不写入数据库')
    args = parser.parse_args()
    hotel_id = os.environ.get('HOTEL_ID', '').strip()
    database = os.environ.get('HOTEL_OTA_MYSQL_DATABASE', '').strip()
    if not hotel_id or not database:
        raise ValueError('请先配置内部酒店ID和数据库')
    context = request('booking_context')
    key = '|'.join((os.environ.get('HOTEL_OTA_MYSQL_HOST', ''), os.environ.get('HOTEL_OTA_MYSQL_PORT', ''),
                    database, hotel_id, context['hotel_code']))
    output = Path(os.environ.get('HOTEL_OTA_OUTPUT_DIR') or Path(__file__).resolve().parents[1] / 'OTA数据') / 'huiyun'
    checkpoint = output / 'jd01_checkpoints.json'
    checkpoints = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {}
    now = datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    start, end = collection_window(now.date(), key in checkpoints)
    payload = request(f'booking|{start.isoformat()}|{end.isoformat()}', timeout=240)
    if payload.get('hotel_code') != context['hotel_code']:
        raise ValueError('慧云查询期间酒店已切换')
    rows = transform(payload, hotel_id, now, start, end)
    if not args.dry_run:
        sync_mysql(rows, hotel_id, start, end, now)
        output.mkdir(parents=True, exist_ok=True)
        checkpoints[key] = now.isoformat(timespec='seconds')
        checkpoint.write_text(json.dumps(checkpoints, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"慧云 JD01 {'预览' if args.dry_run else '入库'}完成：预计到店 {start} 至 {end}，{len(rows)} 条")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
