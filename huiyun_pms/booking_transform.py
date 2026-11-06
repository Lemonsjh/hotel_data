"""慧云账户按预订号、房型和房价汇总；同住账户不重复计算房数。"""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation


def clean(value):
    return str(value or '').strip()


def timestamp(value):
    try:
        return datetime.fromisoformat(clean(value))
    except ValueError:
        raise ValueError('慧云预订到离日期无效') from None


def price(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise ValueError('慧云预订房价无效') from None


def transform(payload, hotel_id, snapshot, start, end):
    if payload.get('ok') is not True or not hotel_id or not payload.get('hotel_code') or not payload.get('hotel_name'):
        raise ValueError('慧云预订酒店或登录状态无效')
    if payload.get('start_date') != start.isoformat() or payload.get('end_date') != end.isoformat():
        raise ValueError('慧云预订查询日期不一致')
    records, types = payload.get('records'), payload.get('room_types')
    if not isinstance(records, list) or not isinstance(types, dict):
        raise ValueError('慧云预订数据或房型资料缺失')
    rooms, accounts = defaultdict(list), {}
    for item in records:
        account, order, room_type = (clean(item.get(k)) for k in ('acct_num', 'resv_num', 'rm_typ'))
        if not room_type and clean(item.get('AcctStus')) == '团队':
            continue  # 团队主账户不代表客房，明细另有房型账户。
        room, share = clean(item.get('rm_num')), clean(item.get('shr_num'))
        arrival, departure = timestamp(item.get('arr_dt')), timestamp(item.get('dpt_dt'))
        if not account or not order or room_type not in types or not clean(types[room_type]):
            raise ValueError('慧云预订账号、预订号或房型缺失')
        if not start <= arrival.date() <= end or departure < arrival or not (room or share):
            raise ValueError('慧云预订日期或房间标识无效')
        try:
            count = int(item.get('rm_nums'))
        except (ValueError, TypeError):
            raise ValueError('慧云预订房数无效') from None
        if count <= 0:
            raise ValueError('慧云预订房数无效')
        record = (order, room_type, arrival, departure, room or ('共享:' + share), price(item.get('rt_amt')), count,
                  clean(item.get('AcctStus')), clean(item.get('rt_cd')))
        if account in accounts:
            if accounts[account] != record:
                raise ValueError('慧云预订同一账户返回冲突记录')
            continue
        accounts[account] = record
        rooms[record[:5]].append(record)
    merged = {}
    for (order, room_type, arrival, departure, _), entries in rooms.items():
        prices = {entry[5] for entry in entries if entry[5] != 0}
        if len(prices) > 1:
            raise ValueError('慧云同一房间存在多个非零房价')
        room_price = next(iter(prices), Decimal(0))
        key = (order, room_type, room_price, arrival, departure)
        if key not in merged:
            merged[key] = {'hotel_id': hotel_id, 'hotel_name': clean(payload['hotel_name']), 'source_platform': 'PMS（慧云）',
                'order_id': order, 'booking_time': None, 'contact': None, 'guest_source': None, 'member_level': None,
                'arrival_time': arrival, 'departure_time': departure, 'room_type_name': clean(types[room_type]),
                'room_type_id': None, 'room_count': 0, 'price_type': None, 'room_price': room_price,
                'prepayment': None, 'guarantee_method': None, 'booking_status': '', 'hold_time': None,
                'remarks': None, 'operator_name': None, 'snapshot_time': snapshot, '_statuses': set()}
        row = merged[key]
        row['room_count'] += max(entry[6] for entry in entries)
        row['_statuses'].update(entry[7] for entry in entries if entry[7])
    for row in merged.values():
        row['booking_status'] = '、'.join(sorted(row.pop('_statuses'))) or None
    return list(merged.values())
