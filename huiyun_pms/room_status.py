"""慧云当前房态转换；只处理房间和房型，不处理客人资料。"""
from collections import Counter
from datetime import date, datetime
from zoneinfo import ZoneInfo
from huiyun_pms.revenue import apply_revenue

SOURCE = 'PMS（慧云）'
STATES = {'V': '空房', 'O': '在住', 'A': '临时分配', 'U': '维修', 'S': '暂停使用'}


def text(value):
    return '' if value is None else str(value).strip()


def table(payload, report, name):
    tables = (payload.get('reports') or {}).get(report)
    if not isinstance(tables, list):
        raise ValueError(f'慧云 {report} 响应缺失')
    matches = [item for item in tables if item.get('name') == name]
    if len(matches) != 1 or not isinstance(matches[0].get('records'), list):
        raise ValueError(f'慧云 {name} 数据表缺失或重复')
    return matches[0]['records']


def transform(payload):
    if payload.get('ok') is not True or not text(payload.get('hotel_code')):
        raise ValueError('慧云酒店或登录上下文缺失')
    code = text(payload['hotel_code'])
    business_date = date.fromisoformat(payload['business_date'])
    stamp = datetime.fromisoformat(payload['captured_at'])
    stamp = stamp.replace(tzinfo=ZoneInfo('Asia/Shanghai')) if stamp.tzinfo is None else stamp.astimezone(ZoneInfo('Asia/Shanghai'))
    types = {}
    for item in table(payload, 'GetRoomtypUsing', 'sysconf'):
        key, name = text(item.get('para_cd')), text(item.get('para_drpt'))
        if not key or not name or key in types:
            raise ValueError('慧云房型字典名称、代码缺失或重复')
        types[key] = name
    rooms = table(payload, 'GetRoomInfoDataCompress', 'roomstus')
    if not rooms:
        raise ValueError('慧云房态为空，未生成新快照')
    groups, seen = {}, {}
    excluded = 0
    for room in rooms:
        if text(room.get('htl_cd')) != code:
            raise ValueError('慧云响应包含其他酒店的房间')
        room_type, number = text(room.get('rm_typ')), text(room.get('rm_num'))
        if not number or room_type not in types:
            raise ValueError('慧云房号或房型字典匹配失败')
        # ** 为客户端的哑房类型，不计入实际客房数。
        if room_type == '**':
            excluded += 1
            continue
        state = text(room.get('rm_stus'))
        clean, reserved = text(room.get('cln_stus')), text(room.get('todayresvflg'))
        if state not in STATES or reserved not in {'', '0', '1'}:
            raise ValueError(f'慧云房态代码未确认：{state} / {reserved}')
        identity = (text(room.get('bld_cd')), number)
        signature = (room_type, state, clean, reserved)
        if identity in seen:
            if seen[identity] != signature:
                raise ValueError('同一房间出现冲突房态')
            continue
        seen[identity] = signature
        group = groups.setdefault(room_type, Counter())
        group['total_rooms'] += 1
        if state == 'O':
            group['in_house_rooms'] += 1
            group['occupied_rooms'] += 1
        elif state == 'V':
            group['vacant_rooms'] += 1
            if 'todayresvflg' not in room:
                raise ValueError('空房的当日预订标识缺失，不能推算可售数')
            if clean not in {'0', '1', '2', '3'}:
                raise ValueError('慧云清扫状态未确认')
            group['vacant_clean_rooms' if clean in {'0', '3'} else 'vacant_dirty_rooms'] += 1
            # 该标记只有1表示当日预订；数据库NULL表示没有预订标记。
            if reserved == '1':
                group['reserved_rooms'] += 1
                group['occupied_rooms'] += 1
            else:
                group['available_rooms'] += 1
        else:
            field = {'A': 'temporary_allocated_rooms', 'U': 'maintenance_rooms', 'S': 'out_of_service_rooms'}[state]
            group[field] += 1
    if not groups:
        raise ValueError('没有实际客房，未生成新快照')
    metrics = ('total_rooms', 'available_rooms', 'occupied_rooms', 'in_house_rooms', 'reserved_rooms',
               'temporary_allocated_rooms', 'maintenance_rooms', 'out_of_service_rooms',
               'vacant_rooms', 'vacant_clean_rooms', 'vacant_dirty_rooms')
    rows = []
    for key, counts in sorted(groups.items()):
        rows.append({
            'hotel_id': None, 'pms_hotel_code': code, 'hotel_name': text(payload.get('hotel_name')),
            'source_platform': SOURCE, 'snapshot_time': stamp.isoformat(),
            'snapshot_hour': stamp.replace(minute=0, second=0, microsecond=0).isoformat(),
            'stay_date': business_date.isoformat(), 'pms_room_type_id': key,
            'room_type_name': types[key], 'room_type_id': None,
            **{field: counts[field] for field in metrics},
            'overbooking_rooms': None, 'room_revenue': None, 'adr': None, 'revpar': None,
        })
    financials = apply_revenue(rows, table(payload, 'GetRoomUsedInfoObj', 'room_revenue'), business_date.isoformat())
    return {'mode': 'preview', 'database_written': False, 'scope': 'current_client_permitted_buildings', 'hotel_code': code,
            'hotel_name': text(payload.get('hotel_name')), 'snapshot_time': stamp.isoformat(),
            'business_date': business_date.isoformat(),
            'excluded_dummy_rooms': excluded, 'room_type_count': len(rows),
            'summary': {**{field: sum(row[field] for row in rows) for field in metrics}, **financials},
            'room_types': rows}
