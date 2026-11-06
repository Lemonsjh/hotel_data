"""慧云 RS01 房费：首次最近30天，后续昨日，每日成功一次。"""
import argparse
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from huiyun_pms.booking_transform import clean, timestamp
from huiyun_pms.bridge import request
from huiyun_pms.daily_checkpoint import DailyCheckpoint
from huiyun_pms.daily_report import now_shanghai, rounded
from huiyun_pms.revenue_storage import SOURCE, sync_mysql


def collection_window(today, initialized):
    end = today - timedelta(days=1)
    return end - timedelta(days=0 if initialized else 29), end


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise ValueError('慧云RS01金额或财务间夜无效') from None


def actual_time(value):
    if not clean(value):
        return None
    result = timestamp(value)
    return result if result.year > 1900 else None


def transform(payload, hotel_id, context, start, end, snapshot):
    if (payload.get('ok') is not True or not hotel_id or not context.get('hotel_code') or not context.get('hotel_name')
            or payload.get('hotel_code') != context['hotel_code'] or payload.get('hotel_name') != context['hotel_name']
            or payload.get('start_date') != str(start) or payload.get('end_date') != str(end)):
        raise ValueError('慧云RS01酒店或查询日期不符')
    transactions, accounts, types, rooms = (payload.get(key) for key in ('transactions', 'accounts', 'room_types', 'rooms'))
    if not isinstance(transactions, list) or any(not isinstance(data, dict) for data in (accounts, types, rooms)):
        raise ValueError('慧云RS01明细或账户资料缺失')
    grouped, seen = {}, set()
    for item in transactions:
        transaction_id = clean(item.get('trn_id'))
        day = timestamp(item.get('trn_dt')).date()
        account_id = clean(item.get('orgacct_num')) or clean(item.get('acct_num'))
        room = clean(item.get('rm_num'))
        if (not transaction_id or transaction_id in seen or not start <= day <= end
                or clean(item.get('trn_cd')) != '100' or clean(item.get('trn_drpt')) != '房费'
                or account_id not in accounts or not room):
            raise ValueError('慧云RS01交易重复、科目或日期/账户无效')
        seen.add(transaction_id)
        account = accounts[account_id]
        if clean(account.get('acct_num')) != account_id or clean(account.get('htl_cd')) != context['hotel_code']:
            raise ValueError('慧云RS01账户关联不符')
        room_type = rooms.get(room)
        if not room_type and clean(account.get('rm_num')) == room:
            room_type = clean(account.get('rm_typ'))
        fee, nights = number(item.get('trn_amt')), number(item.get('financenights'))
        order = clean(account.get('resv_num'))
        row = {'hotel_id': hotel_id, 'hotel_name': context['hotel_name'], 'source_platform': SOURCE,
               'business_date': day, 'room_no': room, 'room_type_name': clean(types.get(room_type)) or None,
               'room_type_id': None, 'guest_name': clean(account.get('guest_name')) or None,
               'customer_source': clean(account.get('customer_source')) or None,
               'checkin_time': actual_time(account.get('chkin_dt')), 'checkout_time': actual_time(account.get('chko_dt')),
               'rack_rate': None, 'price_type': clean(account.get('rt_cd')) or None, 'room_daily_price': None,
               'stay_type': None, 'charge_subject': '房费', 'room_nights': nights, 'room_fee': fee,
               'operator_name': clean(item.get('trn_oper')) or None, 'order_id': order, 'channel_unit_id': None,
               'snapshot_time': snapshot}
        key = (day, order, room, '房费')
        if key not in grouped:
            grouped[key] = (row, account_id, nights)
        else:
            previous, previous_account, weight = grouped[key]
            if not order and account_id != previous_account:
                raise ValueError('慧云RS01缺少订单号，无法区分同房号的不同住宿账户')
            total_fee, total_nights = previous['room_fee'] + fee, previous['room_nights'] + nights
            if nights > weight:
                previous, previous_account, weight = row, account_id, nights
            previous.update(room_fee=total_fee, room_nights=total_nights)
            grouped[key] = (previous, previous_account, weight)
    rows = [row for row, _, _ in grouped.values()]
    if (sum((row['room_fee'] for row in rows), Decimal(0)) != number(payload.get('total_amount'))
            or sum((row['room_nights'] for row in rows), Decimal(0)) != number(payload.get('total_nights'))):
        raise ValueError('慧云RS01交易与汇总金额/间夜不符')
    for row in rows:
        # 有效成交单价取收费事实，不用当前账户房价回填历史日价；零间夜不推算。
        row['room_daily_price'] = rounded(row['room_fee'] / row['room_nights']) if row['room_nights'] > 0 else None
        row['room_fee'], row['room_nights'] = rounded(row['room_fee']), rounded(row['room_nights'])
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='只查询校验，不写数据库或完成标记')
    parser.add_argument('--force', action='store_true', help='忽略今日完成标记，重新采集')
    args = parser.parse_args()
    hotel_id = os.environ.get('HOTEL_ID', '').strip()
    if not hotel_id or not os.environ.get('HOTEL_OTA_MYSQL_DATABASE', '').strip():
        raise ValueError('请先配置内部酒店ID和数据库')
    context = request('booking_context')
    checkpoint = DailyCheckpoint('rs01', hotel_id, context['hotel_code'])
    snapshot = now_shanghai()
    if not args.force and not args.dry_run and checkpoint.done_today(snapshot):
        print('慧云RS01今日已成功采集，跳过')
        return 0
    start, end = collection_window(snapshot.date(), checkpoint.initialized)
    payload = request(f'booking|{start}|{end}|revenue', timeout=240)
    rows = transform(payload, hotel_id, context, start, end, snapshot)
    if now_shanghai().date() != snapshot.date() or request('booking_context')['hotel_code'] != context['hotel_code']:
        raise ValueError('慧云RS01采集跨日或酒店已切换，本轮不写入')
    if not args.dry_run:
        sync_mysql(rows, hotel_id, start, end, snapshot)
        checkpoint.mark(snapshot)
    print(f"慧云RS01{'预览' if args.dry_run else '入库'}完成：{start} 至 {end}，{len(rows)} 条，"
          f"房费 {sum((r['room_fee'] for r in rows), Decimal(0))}，财务间夜 {sum((r['room_nights'] for r in rows), Decimal(0))}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
