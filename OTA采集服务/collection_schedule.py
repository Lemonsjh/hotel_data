"""全量采集按北京时间每小时固定分钟启动。"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MINUTE_CHOICES = {0: '每小时整点', 30: '每小时30分', 50: '每小时50分'}


def collection_minute(settings):
    value = (settings.get('service') or {}).get('collection_minute', 0)
    return value if type(value) is int and value in MINUTE_CHOICES else 0


def now_shanghai():
    return datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)


def next_collection_time(now, minute=0):
    if type(minute) is not int or minute not in MINUTE_CHOICES:
        raise ValueError('采集时刻必须为每小时的00、30或50分')
    target = now.replace(minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(hours=1)
