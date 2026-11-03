import sys
from pathlib import Path
from urllib.parse import urlencode, urlsplit

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[1] / 'OTA采集服务')]
from promotion_balance import main
from meituan_promotion_performance_data import promotion_browser, PAGE_WAIT_SECONDS
from meituan_page_capture import page_access_issue

PAGE_URL = 'https://me.meituan.com/ebooking/merchant/fullPathIframe?' + urlencode({
    'iUrl': 'https://ebmidas.dianping.com/shopdiy/account/pcCpcEntry?continueUrl=/shopdiy-node/main'})
API_PATH = '/shopdiy/common/ajax/queryFinanceInfo'


def fetch_balance():
    with promotion_browser() as context:
        responses = []

        def capture(response):
            url = urlsplit(response.url)
            if url.hostname == 'ebmidas.dianping.com' and url.path == API_PATH:
                responses.append(response)

        context.on('response', capture)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(PAGE_URL, wait_until='domcontentloaded', timeout=60_000)
            for _ in range(PAGE_WAIT_SECONDS * 2):
                if responses:
                    break
                if issue := page_access_issue(page):
                    raise RuntimeError(f'美团推广余额页面需要人工处理：{issue}')
                page.wait_for_timeout(500)
            if not responses:
                raise RuntimeError('美团推广页面未发出余额请求；请检查推广后台登录或手动填写推广 Cookie')
            response = responses[-1]
            if response.status != 200:
                raise RuntimeError(f'美团推广余额接口 HTTP {response.status}')
            return response.json()
        finally:
            context.remove_listener('response', capture)

if __name__ == '__main__':
    raise SystemExit(main('meituan', fetch_balance))
