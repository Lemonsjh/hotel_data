"""慧云 JD02：昨日实际离店客人，逐客保存60天。"""
import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from huiyun_pms.booking_transform import clean, price, timestamp
from huiyun_pms.bridge import request
from huiyun_pms.daily_checkpoint import DailyCheckpoint
from OTA采集服务.pms_guest_storage import sync_guest_details

SOURCE = 'PMS（慧云）'
OTA_GUEST_SOURCES = {
    '上海携程国际旅行社有限公司': '携程',
    '天津西瓜旅游有限责任公司（美团）': '美团',
    '美团': '美团',
}


def transform(payload, hotel_id, day, snapshot):
    if (payload.get('ok') is not True or not hotel_id or not payload.get('hotel_code') or not payload.get('hotel_name')
            or payload.get('departure_date') != day.isoformat()):
        raise ValueError('慧云JD02酒店或离店查询日期不符')
    records, profiles, types = (payload.get(key) for key in ('records', 'profiles', 'room_types'))
    if not isinstance(records, list) or not isinstance(profiles, dict) or not isinstance(types, dict):
        raise ValueError('慧云JD02住店记录、客人资料或房型资料缺失')
    rows, seen = [], {}
    for item in records:
        if clean(item.get('AcctStus')) in {'在店', '预订取消'}:
            continue  # 这些账户可能仍有chko_dt旧值，不属于实际已离店客人。
        account, guest, room_type = (clean(item.get(key)) for key in ('acct_num', 'gh_num', 'rm_typ'))
        arrival, departure = timestamp(item.get('chkin_dt')), timestamp(item.get('chko_dt'))
        if (not account or not guest or guest not in profiles or room_type not in types
                or arrival.year <= 1900 or departure.date() != day or departure < arrival):
            raise ValueError('慧云JD02客人关联或实际入住离店时间无效')
        profile = profiles[guest]
        order_source = clean(item.get('TaNum')) or None
        row = {'hotel_id': hotel_id, 'hotel_name': clean(payload['hotel_name']), 'source_platform': SOURCE,
               'business_date': day, 'order_id': clean(item.get('resv_num')) or None,
               'room_number': clean(item.get('rm_num')) or None, 'room_type_name': clean(types[room_type]),
               'room_type_id': None, 'customer_id': guest, 'customer_name': clean(profile.get('name')) or None,
               'id_card_number': clean(profile.get('id_card')) or None, 'customer_mobile': None,
               'room_price': price(item.get('rt_amt')), 'arrival_time': arrival, 'departure_time': departure,
               'order_source': order_source, 'guest_source': OTA_GUEST_SOURCES.get(order_source),
               'checkin_status': clean(item.get('AcctStus')) or None,
               'main_checkin_id': None, 'main_checkin_person': None, 'snapshot_time': snapshot}
        identity = (account, guest)
        if identity in seen:
            if seen[identity] != row:
                raise ValueError('慧云JD02同一客人账户存在冲突记录')
            continue
        seen[identity] = row
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='只查询校验，不写入数据库')
    parser.add_argument('--force', action='store_true', help='忽略今日已完成标记，重新采集')
    args = parser.parse_args()
    hotel_id = os.environ.get('HOTEL_ID', '').strip()
    if not hotel_id:
        raise ValueError('请配置内部酒店ID')
    snapshot = datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    day = snapshot.date() - timedelta(days=1)
    context = request('booking_context')
    checkpoint = DailyCheckpoint('jd02', hotel_id, context['hotel_code'])
    if not args.force and not args.dry_run and checkpoint.done_today(snapshot):
        print('慧云JD02今日已成功采集，跳过')
        return 0
    payload = request(f'booking|{day.isoformat()}|{day.isoformat()}|guests', timeout=240)
    if payload.get('hotel_code') != context['hotel_code']:
        raise ValueError('慧云JD02查询期间酒店已切换')
    rows = transform(payload, hotel_id, day, snapshot)
    if (datetime.now(ZoneInfo('Asia/Shanghai')).date() != snapshot.date()
            or request('booking_context')['hotel_code'] != context['hotel_code']):
        raise ValueError('慧云JD02采集跨日或酒店已切换，本轮不写入')
    if not args.dry_run:
        sync_guest_details(rows, hotel_id, snapshot, SOURCE, 'pms_huiyun')
        checkpoint.mark(snapshot)
    print(f"慧云JD02{'预览' if args.dry_run else '入库'}完成：离店日期 {day}，{len(rows)} 条客人记录")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
