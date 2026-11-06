"""慧云 JL11：最近30个完整营业日的房型汇总，每日成功一次，仅留最新快照。"""
import argparse
import base64
from datetime import date, timedelta
from decimal import Decimal
import os
from pathlib import Path
import random
import sys
import time

import xlrd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from huiyun_pms.bridge import request
from huiyun_pms.daily_checkpoint import DailyCheckpoint
from huiyun_pms.daily_report import now_shanghai
from huiyun_pms.room_performance_report import number as parse_number, rounded
from huiyun_pms.classification_storage import SOURCE, TABLE, sync_mysql


def number(value):
    try:
        return parse_number(value)
    except ValueError:
        raise ValueError('慧云JL11指标无效') from None


def report_window(today):
    return today - timedelta(days=30), today - timedelta(days=1)


def segments(start, end):
    """年累计在元旦重置，跨年时分别作差，不能直接跨年相减。"""
    result = []
    while start <= end:
        finish = min(end, date(start.year, 12, 31))
        result.append((None if start.month == start.day == 1 else start - timedelta(days=1), finish))
        start = finish + timedelta(days=1)
    return result


def load_cells(payload, hotel_id, context, start, end):
    if (payload.get('ok') is not True or not hotel_id or not context.get('hotel_code') or not context.get('hotel_name')
            or payload.get('hotel_code') != context['hotel_code'] or payload.get('hotel_name') != context['hotel_name']
            or payload.get('start_date') != str(start) or payload.get('end_date') != str(end)):
        raise ValueError('慧云JL11酒店或查询日期不符')
    book = xlrd.open_workbook(file_contents=base64.b64decode(payload['report_base64'], validate=True))
    try:
        return [sheet.row_values(i) for sheet in book.sheets() for i in range(sheet.nrows)]
    finally:
        book.release_resources()


def period_metrics(cells, context, start):
    text = [str(v).strip() for row in cells for v in row]
    if ('房类分析报告月报(权责发生)' not in text or f"酒店代码:{context['hotel_name']}" not in text
            or f'起始日期:{start}' not in text or '月累计(M-T-D)' not in text):
        raise ValueError('慧云JL11房类月报标题、酒店或起日缺失')
    details, totals, current, codes = {}, [], None, set()
    all_nights = all_revenue = excluded = Decimal(0)
    for row in cells:
        if not row:
            continue
        if row[0] == '合计':
            if len(row) < 24 or row[1] != '在店房数' or row[16] != '房费总计':
                raise ValueError('慧云JL11月报合计结构变化')
            totals.append((number(row[2]), number(row[17])))
        if len(row) < 12:
            continue
        label = str(row[3]).strip()
        if label == '在店房数':
            code, name = str(row[1]).strip(), str(row[2]).strip()
            current = {'code': code, 'name': name, 'room_nights': number(row[4])}
            all_nights += current['room_nights']
        elif label == '房费总计':
            if current is None or 'room_revenue' in current:
                raise ValueError('慧云JL11月报收费行未唯一关联房型')
            current['room_revenue'] = number(row[4])
            all_revenue += current['room_revenue']
            if not current['name'] or current['code'] == '**' or current['name'] == '哑房':
                excluded += current['room_revenue']
                continue
            if (not current['code'] or current['name'] in details or current['code'] in codes
                    or current['name'] in {'总计', '合计'}):
                raise ValueError('慧云JL11房型代码或名称重复/缺失')
            codes.add(current['code'])
            details[current['name']] = current
        if row[0] == '平均房价' and current is not None:
            current['average_room_price'] = number(row[1])
    if (not details or len(totals) != 1 or abs(all_nights - totals[0][0]) > Decimal('0.0001')
            or abs(all_revenue - totals[0][1]) > Decimal('0.01')
            or any('average_room_price' not in item or item['room_nights'] < 0 for item in details.values())):
        raise ValueError('慧云JL11月报指标缺失或明细合计不符')
    return details, excluded


def cumulative_metrics(cells, context):
    text = [str(v).strip() for row in cells for v in row]
    if context['hotel_name'] not in text or '房类分析报告日报(权责发生)' not in text:
        raise ValueError('慧云JL11累计日报酒店或标题不符')
    result = {}
    for row in cells:
        if not row or row[0] != '在店房数':
            continue
        if len(row) < 36 or row[18] != '占房率' or row[24] != '房费总计':
            raise ValueError('慧云JL11累计日报列结构变化')
        name = str(row[17]).strip()
        if not name or name == '哑房':
            continue
        if name in result:
            raise ValueError('慧云JL11累计日报房型重复')
        nights, occupancy, revenue = (number(row[i]) for i in (5, 21, 27))
        if nights < 0 or occupancy < 0 or (occupancy == 0 and nights != 0):
            raise ValueError('慧云JL11年累计在店房数或占房率无效')
        room_days = None
        if occupancy > 0:
            inferred = nights * 100 / occupancy
            room_days = inferred.to_integral_value()
            if room_days <= 0 or abs(inferred - room_days) > Decimal('0.0001'):
                raise ValueError('慧云JL11原生累计占房率无法折算完整房日')
        result[name] = {'room_nights': nights, 'room_revenue': revenue, 'room_days': room_days}
    if not result:
        raise ValueError('慧云JL11累计日报无房型')
    return result


def build_rows(details, cumulative, hotel_id, context, start, end, snapshot):
    if any(set(values) != set(details) for values in cumulative.values()):
        raise ValueError('慧云JL11区间与累计日报房型不一致')
    rows = []
    for name, item in details.items():
        nights = revenue = room_days = Decimal(0)
        known_days = True
        for before, finish in segments(start, end):
            if name not in cumulative[finish] or (before is not None and name not in cumulative[before]):
                raise ValueError('慧云JL11区间与累计日报房型不一致')
            high = cumulative[finish][name]
            low = cumulative[before][name] if before else dict(room_nights=Decimal(0), room_revenue=Decimal(0), room_days=Decimal(0))
            nights += high['room_nights'] - low['room_nights']
            revenue += high['room_revenue'] - low['room_revenue']
            if high['room_days'] is None or low['room_days'] is None:
                known_days = False  # 全年从未出租时，0/0无法确定房日；不能用当前房态代替。
            else:
                room_days += high['room_days'] - low['room_days']
        if abs(nights - item['room_nights']) > Decimal('0.0001') or abs(revenue - item['room_revenue']) > Decimal('0.01'):
            raise ValueError('慧云JL11区间房数/房费与累计日报差额不符')
        if known_days and room_days <= 0:
            raise ValueError('慧云JL11累计房日差额无效')
        rows.append({'hotel_id': hotel_id, 'hotel_name': context['hotel_name'], 'source_platform': SOURCE,
                     'snapshot_date': snapshot.date(), 'period_start': start, 'period_end': end, 'section': 'summary',
                     'room_type_id': None, 'room_type_name': name, 'dimension_code': '', 'dimension_name': '',
                     'room_count': room_days if known_days else None, 'room_nights': rounded(item['room_nights']),
                     'occupancy_rate': rounded(item['room_nights'] * 100 / room_days) if known_days else None,
                     'room_revenue': rounded(item['room_revenue']), 'average_room_price': rounded(item['average_room_price']),
                     'revpar': rounded(item['room_revenue'] / room_days) if known_days else None,
                     'overnight_room_count': None, 'overnight_occupancy_rate': None, 'snapshot_time': snapshot})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='仅查询校验，不写数据库或完成标记')
    parser.add_argument('--force', action='store_true', help='忽略今日完成标记')
    args = parser.parse_args()
    hotel_id = os.environ.get('HOTEL_ID', '').strip()
    if not hotel_id or not os.environ.get('HOTEL_OTA_MYSQL_DATABASE', '').strip():
        raise ValueError('请先配置内部酒店ID和数据库')
    context = request('booking_context')
    snapshot = now_shanghai()
    checkpoint = DailyCheckpoint('jl11', hotel_id, context['hotel_code'])
    if not args.force and not args.dry_run and checkpoint.done_today(snapshot):
        print('慧云JL11今日已成功采集，跳过')
        return 0
    start, end = report_window(snapshot.date())
    payload = request(f'booking|{start}|{end}|room_monthly')
    details, excluded = period_metrics(load_cells(payload, hotel_id, context, start, end), context, start)
    cumulative = {}
    boundaries = sorted({day for pair in segments(start, end) for day in pair if day is not None})
    for day in boundaries:
        time.sleep(random.uniform(2, 5))
        payload = request(f'booking|{day}|{day}|room_daily')
        cumulative[day] = cumulative_metrics(load_cells(payload, hotel_id, context, day, day), context)
    rows = build_rows(details, cumulative, hotel_id, context, start, end, snapshot)
    if now_shanghai().date() != snapshot.date() or request('booking_context')['hotel_code'] != context['hotel_code']:
        raise ValueError('慧云JL11采集跨日或酒店已切换，本轮不写入')
    if not args.dry_run:
        sync_mysql(rows, hotel_id, snapshot)
        checkpoint.mark(snapshot)
    unknown = sum(row['room_count'] is None for row in rows)
    print(f"慧云JL11{'预览' if args.dry_run else '入库'}完成：{start} 至 {end}，{len(rows)}个房型，"
          f"房费 {sum(r['room_revenue'] for r in rows)}，在店房数 {sum(r['room_nights'] for r in rows)}；"
          f"无房型/哑房未分摊房费 {excluded}；{unknown}个房型累计房日无法确定", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
