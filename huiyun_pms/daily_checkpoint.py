"""慧云历史报表按酒店/数据库隔离：每日成功一次，失败不记完成。"""
from datetime import datetime
import json
import os
from pathlib import Path


class DailyCheckpoint:
    def __init__(self, report, hotel_id, hotel_code):
        output = Path(os.environ.get('HOTEL_OTA_OUTPUT_DIR') or Path(__file__).resolve().parents[1] / 'OTA数据') / 'huiyun'
        self.path = output / f'{report}_checkpoints.json'
        self.key = '|'.join((os.environ.get('HOTEL_OTA_MYSQL_HOST', ''), os.environ.get('HOTEL_OTA_MYSQL_PORT', ''),
                             os.environ.get('HOTEL_OTA_MYSQL_DATABASE', '').strip(), hotel_id, hotel_code))
        self.values = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    @property
    def initialized(self):
        return self.key in self.values

    def done_today(self, snapshot):
        try:
            return datetime.fromisoformat(self.values.get(self.key, '')).date() == snapshot.date()
        except (TypeError, ValueError):
            return False

    def mark(self, snapshot):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.values[self.key] = snapshot.isoformat(timespec='seconds')
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
