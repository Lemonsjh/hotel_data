"""慧云当前房态采集；--dry-run只生成预览。"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from huiyun_pms.bridge import request
from huiyun_pms.room_status import transform


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='只生成预览，不访问数据库')
    parser.add_argument('--output-dir', type=Path, default=Path(os.environ.get(
        'HOTEL_OTA_OUTPUT_DIR', str(Path(__file__).resolve().parents[1] / 'OTA数据'))) / 'huiyun')
    args = parser.parse_args()
    hotel_id = os.environ.get('HOTEL_ID', '').strip()
    if not args.dry_run and not hotel_id:
        raise ValueError('请先配置内部酒店ID')
    payload = request('room_status')
    preview = transform(payload)
    if not args.dry_run:
        from huiyun_pms.storage import sync_mysql
        for row in preview['room_types']:
            row['hotel_id'] = hotel_id
        sync_mysql(preview['room_types'])
        preview.update(mode='collected', database_written=True)
    result = '未写数据库' if args.dry_run else '已写入当前房态和小时快照'
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in [('huiyun_room_status_raw.json', payload), ('huiyun_room_status_preview.json', preview)]:
        target = args.output_dir / name
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(target)
    lines = ['# 慧云房态预览', '', f"酒店：{preview['hotel_name']}（慧云代码 {preview['hotel_code']}）",
             f"采集时间：{preview['snapshot_time']}；营业日：{preview['business_date']}", '',
             f'{result}；已售数按在住加当日已分房预订统计，临时分配单列。', '',
             '| 房型 | 总房数 | 在住 | 当日预订 | 临时分配 | 维修 | 暂停使用 | 可售 | 预计房费 | ADR | RevPAR |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    fields = ['total_rooms', 'in_house_rooms', 'reserved_rooms', 'temporary_allocated_rooms',
              'maintenance_rooms', 'out_of_service_rooms', 'available_rooms', 'room_revenue', 'adr', 'revpar']
    for row in preview['room_types']:
        lines.append('| ' + row['room_type_name'].replace('|', '\\|') + ' | ' +
                     ' | '.join(str(row[key]) for key in fields) + ' |')
    lines.append('| 合计 | ' + ' | '.join(str(preview['summary'][key]) for key in fields) + ' |')
    lines.extend(['', f"已排除哑房：{preview['excluded_dummy_rooms']}间。"])
    (args.output_dir / 'huiyun_room_status_preview.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    summary = preview['summary']
    print(f"慧云房态采集成功：{preview['room_type_count']}个房型，{summary['total_rooms']}间客房，"
          f"在住{summary['in_house_rooms']}，预订{summary['reserved_rooms']}，可售{summary['available_rooms']}；{result}")
    print(f"Preview: {args.output_dir / 'huiyun_room_status_preview.json'}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
