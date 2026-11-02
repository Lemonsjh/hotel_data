"""别样红 JD11 昨日离店客人明细。"""
import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import requests
import pms_utils
from pms_config import SERVICE_API_BASE_URL
from OTA采集服务.pms_guest_storage import now_shanghai, sync_guest_details

OUTPUT = Path(__file__).resolve().parents[1] / 'output' / 'JD11.json'
SOURCE = 'PMS（别样红）'
FIELDS = {'order_id': 'orderSn', 'room_number': 'roomNumber', 'room_type_name': 'roomType',
          'customer_id': 'customerId', 'customer_name': 'customerName', 'id_card_number': 'idCardNumber',
          'customer_mobile': 'customerMobile', 'checkin_type': 'checkinType', 'order_source': 'orderSource',
          'guest_source': 'source', 'member_level_id': 'memberLevelId', 'member_level': 'memberLevel',
          'oneself_or_not': 'oneselfOrNot', 'price_stakeholder_name': 'priceStackeholderName',
          'price_stakeholder_mobile': 'priceStackeholderMobile', 'order_memo': 'orderMemo',
          'check_out_tip': 'checkOutTip', 'checkin_status': 'checkinStatus', 'operator_name': 'operator',
          'room_attr': 'roomAttr', 'main_checkin_id': 'mainCheckinId'}


def request_body(org_id, day):
    return {'orgId': str(org_id), 'dateType': '2', 'startDate': str(day), 'endDate': str(day),
            'checkinStatus': '', 'checkinType': '', 'operator': '', 'startPrice': '', 'endPrice': '',
            'roomType': '', 'source': '', 'orderSource': '', 'roomAttr': '', 'orderFlagId': '',
            'showTotalCheckinCount': False, 'isNewCustomer': False}


def transform(payload, day, snapshot, hotel_id, hotel_name):
    data = payload.get('data') if isinstance(payload, dict) and payload.get('status') == 0 else None
    variables = data.get('variables') if isinstance(data, dict) else None
    items = data.get('dataList') if isinstance(data, dict) else None
    if (not isinstance(variables, dict) or variables.get('startDate') != str(day)
            or variables.get('endDate') not in (str(day), str(day + timedelta(days=1)))
            or not isinstance(items, list)):
        raise ValueError('JD11 报表日期或明细无效')
    rows = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('JD11 客人记录无效')
        try:
            arrival = datetime.fromisoformat(str(item['arrivalTime']))
            departure = datetime.fromisoformat(str(item['departureTime']))
            price = Decimal(str(item['price'])) if item.get('price') not in (None, '') else None
            flag = int(item['mainCheckinPerson']) if item.get('mainCheckinPerson') is not None else None
            if (arrival.tzinfo or departure.tzinfo or departure < arrival
                    or not day <= departure.date() <= day + timedelta(days=1)
                    or flag not in (0, 1, None) or (price is not None and not price.is_finite())):
                raise ValueError
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise ValueError('JD11 客人时间、房价或主客标记无效') from None
        row = {key: str(item[value]).strip() or None if item.get(value) is not None else None
               for key, value in FIELDS.items()}
        row.update(hotel_id=hotel_id, hotel_name=hotel_name, source_platform=SOURCE,
                   business_date=day, snapshot_time=snapshot, arrival_time=arrival, departure_time=departure,
                   room_price=price, main_checkin_person=flag, total_checkin_count=item.get('totalCheckinCount'))
        rows.append(row)
    return rows


def fetch_jd11():
    info = pms_utils.load_session()
    if not info:
        raise ValueError('JD11 缺少有效登录会话')
    snapshot = now_shanghai()
    day = snapshot.date() - timedelta(days=1)
    session = requests.Session()
    session.trust_env = False
    session.cookies.update(info['cookies'])
    session.headers.update(pms_utils.request_headers(pms_utils.report_url()))
    session.headers['reportCode'] = 'JD11'
    base = SERVICE_API_BASE_URL + '/lion/api/v1/lion/'
    try:
        org_response = session.post(base + 'common/userOrgs', json={}, timeout=pms_utils.API_TIMEOUT_SECONDS)
        org_response.raise_for_status()
        org_data = org_response.json()
        orgs = org_data.get('data') if org_data.get('status') == 0 else None
        if not isinstance(orgs, list):
            raise ValueError('JD11 无法读取酒店列表')
        matches = [org for org in orgs if isinstance(org, dict) and org.get('name') == info.get('hotel_name')]
        if len(matches) != 1 or not matches[0].get('key'):
            raise ValueError('JD11 无法唯一确定当前酒店')
        response = session.post(base + 'reception/jd11', json=request_body(matches[0]['key'], day),
                                timeout=pms_utils.API_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        transform(payload, day, snapshot, os.environ.get('HOTEL_ID', ''), info.get('hotel_name', ''))
        if now_shanghai().date() != snapshot.date():
            raise ValueError('JD11 采集跨日')
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps({'snapshot': snapshot.isoformat(), 'payload': payload}, ensure_ascii=False), encoding='utf-8')
        print('JD11 客人明细已采集')
    finally:
        session.close()


def etl_jd11(connection):
    batch = json.loads(OUTPUT.read_text(encoding='utf-8'))
    snapshot = datetime.fromisoformat(batch['snapshot'])
    hotel_id = os.environ.get('HOTEL_ID', '').strip()
    hotel_name = os.environ.get('PMS_HOTEL_NAME', '').strip()
    rows = transform(batch['payload'], snapshot.date() - timedelta(days=1), snapshot, hotel_id, hotel_name)
    sync_guest_details(rows, hotel_id, snapshot, SOURCE, 'pms_byh', connection)
    print(f'JD11 已写入 {len(rows)} 条客人记录')
