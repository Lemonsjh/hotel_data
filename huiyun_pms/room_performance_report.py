"""慧云 JL01 原生房类日报：首次7天，后续昨日，每日成功一次。"""
import argparse
import base64
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import os
from pathlib import Path
import random
import sys
import time

import xlrd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from huiyun_pms.bridge import request
from huiyun_pms.daily_checkpoint import DailyCheckpoint
from huiyun_pms.daily_report import collection_dates, now_shanghai
from huiyun_pms.room_performance_storage import SOURCE, TABLE, sync_mysql


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise ValueError('慧云JL01当日指标无效') from None


def rounded(value):
    return value.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def parse_cells(cells, hotel_id, context, day, snapshot):
    text = [str(value).strip() for row in cells for value in row]
    if (context['hotel_name'] not in text or '房类分析报告日报(权责发生)' not in text
            or '日累计(TODAY)' not in text or '月累计(M-T-D)' not in text):
        raise ValueError('慧云JL01报表名称、酒店或当日列缺失')
    rows, seen, totals = [], set(), []
    all_nights = all_revenue = excluded_nights = excluded_revenue = Decimal(0)
    for cells_row in cells:
        if not cells_row:
            continue
        label = str(cells_row[0]).strip()
        if label == '合计':
            if (len(cells_row) < 21 or cells_row[1] != '在店房数'
                    or cells_row[13] != '占房率' or cells_row[19] != '房费总计'):
                raise ValueError('慧云JL01合计列结构变化')
            totals.append((number(cells_row[2]), number(cells_row[20])))
        if label != '在店房数':
            continue
        if (len(cells_row) < 36 or cells_row[11] != 'dayuse' or cells_row[18] != '占房率'
                or cells_row[24] != '房费总计' or cells_row[30] != '平均房价'):
            raise ValueError('慧云JL01房型列结构变化')
        # 只读日累计列，月/年累计及同期列不能混入本日。
        nights, occupancy, revenue, adr = (number(cells_row[i]) for i in (1, 19, 25, 31))
        if nights < 0 or occupancy < 0:
            raise ValueError('慧云JL01房数或占房率无效')
        all_nights += nights; all_revenue += revenue
        name = str(cells_row[17]).strip()
        if not name or name == '哑房':
            excluded_nights += nights; excluded_revenue += revenue
            continue
        if name in seen or name in {'合计', '总计'}:
            raise ValueError('慧云JL01房型重复或混入总计')
        seen.add(name)
        rows.append({'hotel_id': hotel_id, 'hotel_name': context['hotel_name'], 'source_platform': SOURCE,
                     'business_date': day, 'room_type_name': name, 'room_type_id': None,
                     'pms_rate_room_type_id': '', 'room_nights': rounded(nights),
                     'occupancy_rate': rounded(occupancy), 'room_revenue': rounded(revenue), 'adr': rounded(adr),
                     'revpar': rounded(adr * occupancy / 100), 'snapshot_time': snapshot})
    if (not rows or len(totals) != 1 or abs(all_nights - totals[0][0]) > Decimal('0.0001')
            or abs(all_revenue - totals[0][1]) > Decimal('0.01')):
        raise ValueError('慧云JL01房型明细与平台合计不符')
    return rows, {'excluded_room_nights': excluded_nights, 'excluded_room_revenue': excluded_revenue}


def transform(payload, hotel_id, context, day, snapshot):
    if (payload.get('ok') is not True or not hotel_id or not context.get('hotel_code') or not context.get('hotel_name')
            or payload.get('hotel_code') != context['hotel_code'] or payload.get('hotel_name') != context['hotel_name']
            or payload.get('business_date') != str(day) or payload.get('start_date') != str(day)
            or payload.get('end_date') != str(day)):
        raise ValueError('慧云JL01酒店或查询日期不符')
    book = xlrd.open_workbook(file_contents=base64.b64decode(payload['report_base64'], validate=True))
    try:
        cells = [sheet.row_values(i) for sheet in book.sheets() for i in range(sheet.nrows)]
    finally:
        book.release_resources()
    return parse_cells(cells, hotel_id, context, day, snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='仅查询校验，不写数据库或完成标记')
    parser.add_argument('--force', action='store_true', help='忽略今日完成标记')
    args = parser.parse_args()
    hotel_id = os.environ.get('HOTEL_ID', '').strip()
    if not hotel_id or not os.environ.get('HOTEL_OTA_MYSQL_DATABASE', '').strip():
        raise ValueError('请先配置内部酒店ID和数据库')
    context = request('booking_context')
    checkpoint = DailyCheckpoint('jl01', hotel_id, context['hotel_code'])
    snapshot = now_shanghai()
    if not args.force and not args.dry_run and checkpoint.done_today(snapshot):
        print('慧云JL01今日已成功采集，跳过')
        return 0
    dates = collection_dates(snapshot.date(), checkpoint.initialized)
    rows = []
    for index, day in enumerate(dates):
        if index:
            time.sleep(random.uniform(2, 5))
        payload = request(f'booking|{day}|{day}|room_daily')
        daily, excluded = transform(payload, hotel_id, context, day, snapshot)
        rows.extend(daily)
        print(f'慧云JL01已校验 {index + 1}/{len(dates)} 天：{day}，{len(daily)}个房型；'
              f'无房型/哑房未分摊房费 {excluded["excluded_room_revenue"]}，房数 {excluded["excluded_room_nights"]}', flush=True)
    if now_shanghai().date() != snapshot.date() or request('booking_context')['hotel_code'] != context['hotel_code']:
        raise ValueError('慧云JL01采集跨日或酒店已切换，本轮不写入')
    if not args.dry_run:
        sync_mysql(rows, hotel_id, dates, snapshot)
        checkpoint.mark(snapshot)
    print(f"慧云JL01{'预览' if args.dry_run else '入库'}完成：{dates[0]} 至 {dates[-1]}，{len(rows)}条")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
