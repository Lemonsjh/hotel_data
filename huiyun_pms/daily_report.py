"""慧云 JY01 总营业指标：首次最近7个完整营业日，后续昨日。"""
import argparse
import base64
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import os
from pathlib import Path
import random
import sys
import time
from zoneinfo import ZoneInfo

import xlrd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from huiyun_pms.bridge import request
from huiyun_pms.daily_checkpoint import DailyCheckpoint
from huiyun_pms.report_storage import sync_report

SOURCE = 'PMS（慧云）'
TABLE = 'jy01_hotel_statistics_daily'
# Crystal 的数据导出会拆分相邻单元格，按已验证的指标列取当日值，禁止扫描后面的累计列。
METRIC_COLUMNS = {'客房总数': 1, '财务间夜数': 2, '房费': 2, '平均房价': 1}


def collection_dates(today, initialized):
    return [today - timedelta(days=n) for n in range(1 if initialized else 7, 0, -1)]


def rounded(value):
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def transform(payload, hotel_id, context, day, snapshot):
    if (payload.get('ok') is not True or not hotel_id or not context.get('hotel_code')
            or not context.get('hotel_name') or payload.get('hotel_code') != context['hotel_code']
            or payload.get('hotel_name') != context['hotel_name'] or payload.get('business_date') != day.isoformat()):
        raise ValueError('慧云JY01酒店或营业日期不符')
    content = base64.b64decode(payload['report_base64'], validate=True)
    book = xlrd.open_workbook(file_contents=content)
    try:
        cells = [sheet.row_values(i) for sheet in book.sheets() for i in range(sheet.nrows)]
    finally:
        book.release_resources()
    text = [str(value).strip() for row in cells for value in row]
    if ('营业状况报表' not in text or f"酒店名称：{context['hotel_name']}" not in text
            or f'统计日期:{day.isoformat()}' not in text
            or not any(row[:2] == ['营业项目', '当日'] for row in cells)):
        raise ValueError('慧云JY01报表名称、酒店、日期或当日列缺失')
    metrics = {}
    for row in cells:
        label = str(row[0]).strip() if row else ''
        if label not in METRIC_COLUMNS:
            continue
        column = METRIC_COLUMNS[label]
        try:
            value = Decimal(str(row[column]))
            if not value.is_finite() or label in metrics:
                raise ValueError
        except (IndexError, InvalidOperation, ValueError):
            raise ValueError(f'慧云JY01 {label} 当日值无效或重复') from None
        metrics[label] = value
    if metrics.keys() != METRIC_COLUMNS.keys():
        raise ValueError('慧云JY01核心指标缺失，保留旧数据')
    total, nights, revenue, adr = (metrics[key] for key in METRIC_COLUMNS)
    if total <= 0 or total != total.to_integral_value() or nights < 0:
        raise ValueError('慧云JY01总房数或财务间夜无效')
    return {'hotel_id': hotel_id, 'hotel_name': context['hotel_name'], 'source_platform': SOURCE,
            'business_date': day, 'dimension_type': '总营业指标', 'dimension_name': '总营业指标',
            'room_type_id': None, 'room_count': int(total), 'room_nights': rounded(nights),
            'room_revenue': rounded(revenue), 'occupancy_rate': rounded(nights / total * 100),
            'adr': rounded(adr), 'revpar': rounded(adr * nights / total),
            'sold_rooms': None, 'remaining_rooms': None, 'orders_today': None, 'snapshot_time': snapshot}


def sync_mysql(rows, hotel_id, dates, snapshot, connection=None):
    return sync_report(rows, hotel_id, dates, snapshot, TABLE, 'business_date', connection)


def now_shanghai():
    return datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='仅查询校验，不写数据库或完成标记')
    parser.add_argument('--force', action='store_true', help='忽略今日已完成标记，重新采集')
    args = parser.parse_args()
    hotel_id = os.environ.get('HOTEL_ID', '').strip()
    database = os.environ.get('HOTEL_OTA_MYSQL_DATABASE', '').strip()
    if not hotel_id or not database:
        raise ValueError('请先配置内部酒店ID和数据库')
    context = request('booking_context')
    checkpoint = DailyCheckpoint('jy01', hotel_id, context['hotel_code'])
    snapshot = now_shanghai()
    if not args.force and not args.dry_run and checkpoint.done_today(snapshot):
        print('慧云JY01今日已成功采集，跳过')
        return 0
    dates = collection_dates(snapshot.date(), checkpoint.initialized)
    rows = []
    for index, day in enumerate(dates):
        if index:
            time.sleep(random.uniform(2, 5))
        payload = request(f'booking|{day}|{day}|daily')
        rows.append(transform(payload, hotel_id, context, day, snapshot))
        print(f'慧云JY01已校验 {index + 1}/{len(dates)} 天：{day}', flush=True)
    if now_shanghai().date() != snapshot.date() or request('booking_context')['hotel_code'] != context['hotel_code']:
        raise ValueError('慧云JY01采集跨日或酒店已切换，本轮不写入')
    if not args.dry_run:
        sync_mysql(rows, hotel_id, dates, snapshot)
        checkpoint.mark(snapshot)
    print(f"慧云JY01{'预览' if args.dry_run else '入库'}完成：{dates[0]} 至 {dates[-1]}，{len(rows)} 天")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
