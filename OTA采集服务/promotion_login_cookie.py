"""人工访问推广通成功后，同步当前登录助手的推广 Cookie。"""
from contextlib import contextmanager
from urllib.parse import urlsplit

import runner


@contextmanager
def watch_promotion_cookie(context, cookie_header, on_saved):
    initial = runner.load_settings().get('meituan') or {}
    identity = {key: initial.get(key) for key in ('me_cookie', 'eb_cookie')}

    def capture(response):
        url = urlsplit(response.url)
        if (url.hostname != 'ebmidas.dianping.com'
                or url.path != '/shopdiy/common/ajax/queryFinanceInfo' or response.status != 200):
            return
        try:
            payload = response.json()
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        data = payload.get('msg')
        if (payload.get('code') != 200 or not isinstance(data, dict)
                or data.get('balance') in (None, '')):
            return
        cookie = cookie_header(context, response.url)
        if not cookie:
            return
        with runner.config_transaction.transaction(runner.CONFIG_PATH):
            settings = runner.load_settings()
            current = settings.setdefault('meituan', {})
            if any(current.get(key) != value for key, value in identity.items()):
                return
            if current.get('promotion_cookie') == cookie:
                return
            current['promotion_cookie'] = cookie
            runner.save_json(runner.CONFIG_PATH, settings)
        on_saved()

    context.on('response', capture)
    try:
        yield
    finally:
        context.remove_listener('response', capture)
