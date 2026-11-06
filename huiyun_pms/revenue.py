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
        if (number(record, 'total') != row['total_rooms'] or
                number(record, 'inHouse') + number(record, 'definiteResv') != row['occupied_rooms']):
            raise ValueError('慧云预计房费与房态房数不一致，请稍后重新采集')
        # 沿用客户端“排除临时预订”的预计房费：ResvAmt - tmpAmt。
        revenue = number(record, 'ResvAmt') - number(record, 'tmpAmt')
        row.update(room_revenue=str(revenue), adr=rate(revenue, row['occupied_rooms']),
                   revpar=rate(revenue, row['total_rooms']))
        total_revenue += revenue
    return {'room_revenue': str(total_revenue),
            'adr': rate(total_revenue, sum(row['occupied_rooms'] for row in rows)),
            'revpar': rate(total_revenue, sum(row['total_rooms'] for row in rows))}
