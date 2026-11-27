"""Refresh Meituan and Dianping pending reviews independently."""
import time
from contextlib import closing
from datetime import datetime

from meituan_review_detail_data import (
    HEADERS, HOTEL_ID, MEITUAN_ME_COOKIE, POI_ID, REVIEW_CHANNELS,
    MeituanReviewDetailClient, normalize_rows,
)
from ota_mysql_writer import connect_mysql
from pending_review_store import channel_lock, replace_channel_snapshot


PENDING_TABLE = "meituan_ota_pending_review_snapshot"
PAGE_SIZE = 10


def collect_channel(client, label, channel):
    count_key = {"meituan": "mtUnRepliedNum", "dianping": "dpUnRepliedNum"}[channel]
    captured_at = datetime.now()
    pending = []
    seen = set()
    page = 1
    while True:
        payload = client.fetch_page(page, PAGE_SIZE, reply_type=3)
        data = payload.get("data") or {}
        count = data.get(count_key)
        if type(count) is not int or count < 0 or data.get("offset") != page or data.get("limit") != PAGE_SIZE:
            raise ValueError("待回复数量或分页信息无效")
        if page == 1:
            total = count
        items = data.get("commentList")
        if count != total or not isinstance(items, list) or len(items) != min(PAGE_SIZE, total - len(seen)):
            raise ValueError("待回复数量变化或分页不完整")
        rows = normalize_rows(payload, label, captured_at)
        if len(rows) != len(items):
            raise ValueError("待回复评价缺少 ID")
        for row in rows:
            review = dict(zip(HEADERS, row, strict=True))
            review_id = review["review_id"]
            if review_id in seen or review["is_replied"]:
                raise ValueError("待回复列表返回重复或已回复评价")
            seen.add(review_id)
            pending.append((review_id, review["review_time"], review["reviewer_name_masked"],
                            review["review_score"], review["review_content"], review["room_type_name"],
                            None, captured_at))
        if len(seen) == total:
            return pending
        page += 1
        time.sleep(0.25)


def main():
    if not HOTEL_ID or not POI_ID or not MEITUAN_ME_COOKIE:
        raise RuntimeError("请配置酒店 ID、美团门店 ID 并登录美团")
    failed = False
    for label, channel, platform, url in REVIEW_CHANNELS:
        try:
            with closing(MeituanReviewDetailClient(MEITUAN_ME_COOKIE, url, platform)) as client:
                pending = collect_channel(client, label, channel)
            with connect_mysql(autocommit=True) as db, db.cursor() as cursor:
                with channel_lock(cursor, HOTEL_ID, channel) as acquired:
                    if not acquired:
                        raise RuntimeError("同渠道正在更新")
                    count = replace_channel_snapshot(db, cursor, PENDING_TABLE, "meituan", HOTEL_ID,
                                                     channel, label, POI_ID, pending)
            print(f"{label}待回复：已刷新 {count} 条", flush=True)
        except Exception as exc:
            failed = True
            reason = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
            print(f"{label}待回复同步失败：{reason}", flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
