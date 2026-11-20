"""把可用房接口的真实预计房费补入当前房态；缺失时不生成快照。"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def number(record, field):
    try:
        value = Decimal(str(record[field]))
        if not value.is_finite():
            raise ValueError
        return value
    except (KeyError, InvalidOperation, ValueError):
        raise ValueError(f'慧云预计房费字段缺失或无效：{field}') from None


def rate(revenue, rooms):
    if rooms == 0:
        if revenue != 0:
            raise ValueError('慧云房费非零但房数为零，不能计算均价')
        return '0.0000'
    return str((revenue / rooms).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))


# 房态只统计已排房的当日预订，房费按订单统计确定预订，未排房时天然会差 1 间。
# 小差异用订单口径校正已售，差异更大说明房型范围或数据本身有问题，仍然拒绝。
MAX_OCCUPIED_DIFF = 2


def apply_revenue(rows, records, business_date):
    by_type = {}
    for record in records:
        key = str(record.get('room_type_id') or '').strip()
        if not key or key in by_type or record.get('stay_date') != business_date:
            raise ValueError('慧云预计房费的房型或日期无效')
        by_type[key] = record
    if set(by_type) != {row['pms_room_type_id'] for row in rows}:
        raise ValueError('慧云预计房费与房态的房型范围不一致')
    total_revenue = Decimal(0)
    for row in rows:
        record = by_type[row['pms_room_type_id']]
        if number(record, 'total') != row['total_rooms']:
            raise ValueError('慧云预计房费与房态房数不一致，请稍后重新采集')
        in_house = number(record, 'inHouse')
        occupied = in_house + number(record, 'definiteResv')
        diff = occupied - row['occupied_rooms']
        if diff:
            if diff != diff.to_integral_value() or abs(diff) > MAX_OCCUPIED_DIFF or in_house != row['in_house_rooms']:
                raise ValueError('慧云预计房费与房态房数不一致，请稍后重新采集')
            reserved = occupied - in_house
            available = row['available_rooms'] - diff
            if reserved < 0 or available < 0:
                raise ValueError('慧云预计房费与房态房数不一致，请稍后重新采集')
            print(f"慧云房态已按房费校正：{row['room_type_name']} 已售 {row['occupied_rooms']}→{occupied}"
                  f"（差 {diff} 间，未排房预订）")
            row.update(occupied_rooms=int(occupied), reserved_rooms=int(reserved), available_rooms=int(available))
        # 沿用客户端“排除临时预订”的预计房费：ResvAmt - tmpAmt。
        revenue = number(record, 'ResvAmt') - number(record, 'tmpAmt')
        row.update(room_revenue=str(revenue), adr=rate(revenue, row['occupied_rooms']),
                   revpar=rate(revenue, row['total_rooms']))
        total_revenue += revenue
    return {'room_revenue': str(total_revenue),
            'adr': rate(total_revenue, sum(row['occupied_rooms'] for row in rows)),
            'revpar': rate(total_revenue, sum(row['total_rooms'] for row in rows))}
