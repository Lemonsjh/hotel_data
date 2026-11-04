"""携程金字塔推广查询、暂停与恢复。"""
from datetime import datetime
import time

import requests

from collection_schema import ensure_tables
from promotion_queue import connect_database, hotel_id

TABLE = 'ctrip_ota_promotion_plan'
BASE_URL = 'https://ebooking.ctrip.com/restapi/soa2/29666/'
PAGE_URL = 'https://ebooking.ctrip.com/advertise/cpc/extensionList'
ACTIONS = {'pause': 3, 'recover': 2}
TARGETS = {'pause': 'PAUSED', 'recover': 'RUNNING'}
PAGE_SIZE = 10


def status_code(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    raise RuntimeError('携程推广状态缺失或无效')


class Client:
    def __init__(self, settings):
        config = settings.get('ctrip') or {}
        if not config.get('enabled', True):
            raise RuntimeError('携程平台未启用')
        cookie = str(config.get('cookie') or '').strip()
        if not cookie:
            raise RuntimeError('请先登录携程并保存 Cookie')
        self.expected_hotel = str(config.get('hotel_id') or '').strip()
        self.session = requests.Session()
        self.session.headers.update({'Cookie': cookie, 'User-Agent': 'Mozilla/5.0',
                                     'Referer': PAGE_URL, 'Origin': 'https://ebooking.ctrip.com'})

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.session.close()

    def request(self, endpoint, **fields):
        payload = {
            'reqHead': {'host': 'ebooking.ctrip.com', 'pathName': '/advertise/cpc/extensionList',
                        'locale': 'zh-CN', 'protocal': 'https:'},
            'head': {'cver': '1.0', 'lang': '01', 'sid': '8888', 'syscode': '09', 'extension': []},
            **fields,
        }
        try:
            response = self.session.post(BASE_URL + endpoint, json=payload, timeout=30, allow_redirects=False)
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError):
            raise RuntimeError('携程推广接口请求失败，请检查登录状态') from None
        if (not isinstance(result, dict) or (result.get('resStatus') or {}).get('rcode') != 200
                or (result.get('ResponseStatus') or {}).get('Ack') != 'Success'):
            raise RuntimeError('携程推广接口未返回成功状态')
        return result

    def promotions(self):
        plans = {}
        accounts = set()
        total = None
        for index in range(100):
            result = self.request('queryPromotionDetails', pageIndex=index, pageSize=PAGE_SIZE,
                                  status=[2, 1, 3, 4, 6, 7])
            items = result.get('data')
            count, pages = result.get('totalRecord'), result.get('totalPage')
            if (not isinstance(items, list) or type(count) is not int or count < 0
                    or type(pages) is not int or pages < 0 or (count > 0 and pages == 0)
                    or (total is not None and total != count)):
                raise RuntimeError('携程推广列表分页数据不完整')
            total = count
            for item in items:
                identifier = str(item.get('campaignId') or '')
                account = str(item.get('accountId') or '')
                if not identifier.isdigit() or not account.isdigit() or identifier in plans:
                    raise RuntimeError('携程推广列表的计划或酒店 ID 无效')
                if self.expected_hotel and account != self.expected_hotel:
                    raise RuntimeError('携程推广所属酒店与配置不符')
                accounts.add(account)
                if len(accounts) > 1:
                    raise RuntimeError('携程推广列表包含多家酒店，请切换到单酒店账号')
                raw_status = status_code(item.get('campaignStatus'))
                plans[identifier] = {
                    'launch_id': identifier, 'promotion_name': item.get('campaignName') or identifier,
                    'account_id': account, 'campaign_status': raw_status,
                    'promotion_status': 'PAUSED' if raw_status == 3 else 'RUNNING',
                    'snapshot_time': datetime.now(),
                }
            if index + 1 >= pages:
                if len(plans) != total:
                    raise RuntimeError('携程推广列表分页数量不符')
                return list(plans.values())
            if not items:
                raise RuntimeError('携程推广列表分页被截断')
            time.sleep(1)
        raise RuntimeError('携程推广列表超过分页上限')


def find(plans, identifier):
    for plan in plans:
        if plan['launch_id'] == str(identifier):
            return plan
    raise RuntimeError('当前携程账号下未找到目标推广计划')


def current_promotions(settings):
    connection = connect_database(settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT campaign_id AS launch_id, campaign_name AS promotion_name, account_id, '
                f'campaign_status, promotion_status, snapshot_time FROM `{TABLE}` '
                'WHERE hotel_id=%s ORDER BY campaign_id', (hotel_id(settings, 'ctrip'),))
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()


def save_snapshot(settings, plans):
    scope = hotel_id(settings, 'ctrip')
    name = str((settings.get('ctrip') or {}).get('hotel_name') or '')
    ensure_tables(settings.get('mysql') or {}, [TABLE])
    connection = connect_database(settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'DELETE FROM `{TABLE}` WHERE hotel_id=%s', (scope,))
            if plans:
                cursor.executemany(
                    f'INSERT INTO `{TABLE}` (hotel_id, hotel_name, campaign_id, campaign_name, '
                    'account_id, campaign_status, promotion_status, snapshot_time) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                    [(scope, name, plan['launch_id'], plan['promotion_name'], plan['account_id'],
                      plan['campaign_status'], plan['promotion_status'], plan['snapshot_time']) for plan in plans])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def collect_snapshot(settings):
    with Client(settings) as client:
        plans = client.promotions()
    save_snapshot(settings, plans)
    return len(plans)


def find_promotion(settings, identifier):
    return find(current_promotions(settings), identifier)


def control_promotion(settings, promotion, action):
    if action not in ACTIONS:
        raise ValueError('不支持的推广操作')
    with Client(settings) as client:
        plans = client.promotions()
        current = find(plans, promotion['launch_id'])
        if current['account_id'] != promotion['account_id']:
            raise RuntimeError('携程推广所属酒店已变化')
        if current['promotion_status'] != TARGETS[action]:
            result = client.request('modifyPromotionStatus', campaignId=int(current['launch_id']), status=ACTIONS[action])
            if result.get('data') is not True:
                raise RuntimeError('携程未确认推广操作成功，请到平台核对')
            plans = client.promotions()
            current = find(plans, current['launch_id'])
            if current['account_id'] != promotion['account_id']:
                raise RuntimeError('携程推广所属酒店已变化')
            if current['promotion_status'] != TARGETS[action]:
                raise RuntimeError('携程推广状态尚未达到目标，请到平台核对；未自动重试')
    save_snapshot(settings, plans)
    label = '已关闭' if action == 'pause' else '已开启'
    return f'携程推广{label}（原始状态：{current["campaign_status"]}）'
