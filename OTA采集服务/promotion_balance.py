"""美团推广通与携程金字塔账户余额的最新快照。"""
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests

from ota_mysql_writer import sync_table

TABLE = 'ota_promotion_balance'
HEADERS = ['hotel_id', 'platform_scope', 'promotion_type', 'promotion_type_name',
           'total_balance', 'account_balance', 'coupon_balance', 'currency', 'snapshot_time']
URLS = {
    'meituan': 'https://ebmidas.dianping.com/shopdiy/common/ajax/queryFinanceInfo',
    'ctrip': 'https://ebooking.ctrip.com/toolcenter/api/specialAccount/fetchSpecialAccount?hostType=HE',
}


def amount(value, *, required=False):
    if value is None or value == '':
        if required:
            raise RuntimeError('余额接口缺少账户余额')
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise RuntimeError('余额接口返回了无效金额') from None


def build_row(platform, payload, hotel_id, captured_at):
    if not hotel_id:
        raise RuntimeError('请先配置内部酒店 ID')
    if not isinstance(payload, dict):
        raise RuntimeError('余额接口响应无效')
    if platform == 'meituan':
        data = payload.get('msg')
        if payload.get('code') != 200 or not isinstance(data, dict):
            raise RuntimeError('美团推广通余额接口未成功，请检查推广后台登录状态')
        return [hotel_id, platform, 'promotion_tong', '推广通', amount(data.get('balance'), required=True),
                None, None, 'CNY', captured_at]
    if platform == 'ctrip':
        data = payload.get('data')
        if payload.get('code') != 0 or not isinstance(data, dict) or data.get('specialAccountType') != 'Pyramid':
            raise RuntimeError('携程金字塔余额接口未成功，请检查登录状态')
        return [hotel_id, platform, 'pyramid', '金字塔', amount(data.get('totalBalance'), required=True),
                amount(data.get('accountBalance')), amount(data.get('couponBalance')),
                data.get('currency') or 'CNY', captured_at]
    raise ValueError('不支持的平台')


def fetch_balance(platform):
    cookie_key = 'MEITUAN_PROMOTION_COOKIE' if platform == 'meituan' else 'CTRIP_COOKIE'
    cookie = os.environ.get(cookie_key, '').strip()
    if not cookie:
        raise RuntimeError('请先配置平台登录 Cookie')
    headers = {'Cookie': cookie, 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json, text/plain, */*',
               'Referer': ('https://ebmidas.dianping.com/shopdiy-node/main' if platform == 'meituan'
                           else 'https://ebooking.ctrip.com/toolcenter/cpc/pyramid')}
    try:
        if platform == 'meituan':
            response = requests.get(URLS[platform], headers=headers, timeout=30, allow_redirects=False)
        else:
            response = requests.post(URLS[platform], headers=headers, timeout=30, allow_redirects=False,
                                     data={'accountType': 'Hotel', 'specialAccountType': 'Pyramid'})
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        raise RuntimeError(f'{platform} 推广余额接口请求失败或登录已失效') from None


def main(platform):
    row = build_row(platform, fetch_balance(platform), os.environ.get('HOTEL_ID', '').strip(), datetime.now())
    sync_table(TABLE, HEADERS, [row])
    print(f'{platform} promotion balance snapshot saved')
    return 0
