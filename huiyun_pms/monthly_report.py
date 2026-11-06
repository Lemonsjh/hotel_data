"""慧云 JY03：首次最近3个月及去年对应月，后续更新昨日所在月及去年同期。"""
import argparse
import base64
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import sys
import time

import xlrd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from huiyun_pms.bridge import request
from huiyun_pms.daily_checkpoint import DailyCheckpoint
from huiyun_pms.daily_report import now_shanghai, rounded
from huiyun_pms.report_storage import SOURCE, sync_report

TABLE = 'jy03_hotel_statistics_month'
HEADERS = ['日期', '总房数', '总出租房数', '财务间夜数', '房费', '平均房价', '出租率', '餐费', '总消费', 'RevPAR']


def collection_windows(today, initialized):
    end = today - timedelta(days=1)
    anchor = end if initialized else today
    current = anchor.year * 12 + anchor.month - 1
    windows = []
    for offset in range(1 if initialized else 3):
        year, zero_month = divmod(current - offset, 12)
        start = date(year, zero_month + 1, 1)
        last = date(year, start.month, monthrange(year, start.month)[1])
        finish = min(last, end)
        if start > finish:  # 本月尚无完整营业日时，同期窗口也不查询。
            continue
        previous_last = monthrange(year - 1, start.month)[1]
        previous_day = previous_last if finish == last else min(finish.day, previous_last)
        windows.extend([(start, finish), (date(year - 1, start.month, 1), date(year - 1, start.month, previous_day))])
    return sorted(windows)


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise ValueError('慧云JY03房数、财务间夜或房费无效') from None


def transform(payload, hotel_id, context, start, end, snapshot):
    if (payload.get('ok') is not True or not hotel_id or not context.get('hotel_code') or not context.get('hotel_name')
            or payload.get('hotel_code') != context['hotel_code'] or payload.get('hotel_name') != context['hotel_name']
            or payload.get('start_date') != str(start) or payload.get('end_date') != str(end) or start > end
            or start.day != 1 or (start.year, start.month) != (end.year, end.month)):
        raise ValueError('慧云JY03酒店或查询范围不符')
    book = xlrd.open_workbook(file_contents=base64.b64decode(payload['report_base64'], validate=True))
    try:
        cells = [sheet.row_values(i) for sheet in book.sheets() for i in range(sheet.nrows)]
    finally:
        book.release_resources()
    text = [str(value).strip() for row in cells for value in row]
    if ('营业收入月报' not in text or f"酒店名称：{context['hotel_name']}" not in text
            or f'起始日期:{start}' not in text or not any(row[:10] == HEADERS for row in cells)):
        raise ValueError('慧云JY03报表标题、酒店、日期或表头缺失')
    details = [row for row in cells if row and re.fullmatch(r'\d{2}\([一二三四五六日]\)', str(row[0]).strip())]
    if len(details) != (end - start).days + 1:
        raise ValueError('慧云JY03营业日期不完整，保留旧数据')
    for offset, row in enumerate(details):
        day = start + timedelta(days=offset)
        # 源表只显示日号及星期，按请求起日逐日核对，避免跨月日号混淆。
        if str(row[0]).strip() != f'{day.day:02d}({"一二三四五六日"[day.weekday()]})':
            raise ValueError('慧云JY03营业日顺序不符')
        values = [number(row[column]) for column in (1, 3, 4)]
        if values[0] < 0 or values[0] != values[0].to_integral_value() or values[1] < 0:
            raise ValueError('慧云JY03房数或财务间夜无效')
    summaries = [row for row in cells if row and str(row[0]).strip() == '总计']
    if len(summaries) != 1 or any(rounded(sum(number(row[col]) for row in details)) != rounded(number(summaries[0][col]))
                                  for col in (1, 4)):
        raise ValueError('慧云JY03房量、房费明细与总计不符')
    # 财务间夜的逐日值与总计可能有舍入差异，直接采用平台该月总计。
    total, nights, revenue = (number(summaries[0][col]) for col in (1, 3, 4))
    if total == nights == revenue == 0:
        return []
    if total <= 0 or total != total.to_integral_value() or nights < 0 or (nights == 0 and revenue != 0):
        raise ValueError('慧云JY03月指标缺少可计算的房量或间夜')
    return [{'hotel_id': hotel_id, 'hotel_name': context['hotel_name'], 'source_platform': SOURCE,
             'period_month': start.strftime('%Y-%m'), 'dimension_type': '总营业指标', 'dimension_name': '总营业指标',
             'room_type_id': None, 'room_count': int(total), 'room_nights': rounded(nights),
             'room_revenue': rounded(revenue), 'maintain_rooms': None,
             'occupancy_rate': rounded(nights / total * 100),
             'adr': rounded(revenue / nights) if nights else Decimal('0.00'),
             'revpar': rounded(revenue / total), 'snapshot_time': snapshot}]


def sync_mysql(rows, hotel_id, snapshot, connection=None):
    return sync_report(rows, hotel_id, [row['period_month'] for row in rows], snapshot,
                       TABLE, 'period_month', connection)


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
    checkpoint = DailyCheckpoint('jy03', hotel_id, context['hotel_code'])
    snapshot = now_shanghai()
    if not args.force and not args.dry_run and checkpoint.done_today(snapshot):
        print('慧云JY03今日已成功采集，跳过')
        return 0
    windows = collection_windows(snapshot.date(), checkpoint.initialized)
    rows = []
    for index, (start, end) in enumerate(windows):
        if index:
            time.sleep(3)
        payload = request(f'booking|{start}|{end}|monthly')
        rows.extend(transform(payload, hotel_id, context, start, end, snapshot))
        print(f'慧云JY03已校验：{start} 至 {end}', flush=True)
    target = windows[-1][1].strftime('%Y-%m')
    if not rows or not any(row['period_month'] == target for row in rows):
        raise ValueError('慧云JY03未返回目标月数据，保留旧数据')
    if now_shanghai().date() != snapshot.date() or request('booking_context')['hotel_code'] != context['hotel_code']:
        raise ValueError('慧云JY03采集跨日或酒店已切换，本轮不写入')
    if not args.dry_run:
        sync_mysql(rows, hotel_id, snapshot)
        checkpoint.mark(snapshot)
    print(f"慧云JY03{'预览' if args.dry_run else '入库'}完成：{rows[0]['period_month']} 至 {rows[-1]['period_month']}，{len(rows)} 个月")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
