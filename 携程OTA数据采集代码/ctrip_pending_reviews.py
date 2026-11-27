"""Collect the platform's current pending reviews by channel."""
from __future__ import annotations

import time
from datetime import datetime

from ctrip_review_detail_data import (
    CHANNELS, COOKIE, HEADERS, HOTEL_ID, INTERNAL_HOTEL_ID,
    CtripReviewDetailClient, CtripReviewDetailError, normalize_rows,
)
from ota_mysql_writer import connect_mysql
from pending_review_store import TASK_TABLE, channel_lock, replace_channel_snapshot, table_exists


PENDING_TABLE = "ctrip_ota_pending_review_snapshot"
PAGE_SIZE = 10


def pending_pages(client, source, expected_hotel=""):
    seen = set()
    page = 1
    while True:
        result = client.fetch_page(page, PAGE_SIZE, source, pending=True)
        response = result.get("ResponseStatus") or {}
        status = result.get("resStatus") or {}
        if response.get("Ack") != "Success" or response.get("Errors") or str(status.get("rcode")) != "200":
            raise CtripReviewDetailError("待回复接口未确认成功")
        hotel = str(result.get("masterHotelId") or "").strip()
        if not hotel.isdecimal() or (expected_hotel and hotel != expected_hotel):
            raise CtripReviewDetailError("待回复接口返回的门店不符")
        expected_hotel = hotel
        total, pages, index = (result.get(key) for key in ("commentCount", "pageCount", "currentPageIndex"))
        if not all(type(value) is int for value in (total, pages, index)) or total < 0 or index != page:
            raise CtripReviewDetailError("待回复接口分页数据无效")
        if page == 1:
            first_total, first_pages = total, pages
        if (total, pages) != (first_total, first_pages) or (total == 0 and pages not in (0, 1)) or (
            total > 0 and pages != (total + PAGE_SIZE - 1) // PAGE_SIZE
        ):
            raise CtripReviewDetailError("待回复接口分页总数不符")
        rows = result.get("commentlist")
        expected_rows = min(PAGE_SIZE, total - len(seen))
        if not isinstance(rows, list) or len(rows) != expected_rows:
            raise CtripReviewDetailError("待回复接口返回条数不足")
        for row in rows:
            if not isinstance(row, dict) or str(row.get("sourceId")) != source:
                raise CtripReviewDetailError("待回复评价渠道不符")
            review_id = str(row.get("commentId") or "").strip()
            if not review_id or review_id in seen:
                raise CtripReviewDetailError("待回复评价 ID 缺失或重复")
            seen.add(review_id)
        yield hotel, rows
        if page >= pages:
            return
        page += 1
        time.sleep(0.15)


def collect_channel(client, channel, expected_hotel):
    source, label = CHANNELS[channel]
    reviews = []
    for hotel, rows in pending_pages(client, source, expected_hotel):
        reviews.extend(rows)
    captured_at = datetime.now()
    normalized = normalize_rows({"masterHotelId": hotel, "commentlist": reviews}, label, captured_at)
    if len(reviews) != len(normalized):
        raise CtripReviewDetailError("待回复评价解析条数不符")
    pending = []
    for raw, values in zip(reviews, normalized, strict=True):
        review = dict(zip(HEADERS, values, strict=True))
        if review["is_replied"]:
            raise CtripReviewDetailError("待回复接口返回了已回复评价")
        enabled = raw.get("enableReply")
        pending.append((str(raw["commentId"]).strip(), review["review_time"], review["reviewer_name_masked"],
                        review["review_score"], review["review_content"], review["room_type_name"],
                        enabled if isinstance(enabled, bool) else None, captured_at))
    return hotel, pending


def replace_snapshot(db, cursor, hotel_id, channel, poi_id, pending):
    return replace_channel_snapshot(db, cursor, PENDING_TABLE, "ctrip", hotel_id, channel,
                                    CHANNELS[channel][1], poi_id, pending)


def sync_pending_reviews(client, hotel_id, expected_hotel=""):
    failed = False
    with connect_mysql(autocommit=True) as db, db.cursor() as cursor:
        for channel in CHANNELS:
            try:
                with channel_lock(cursor, hotel_id, channel) as locked:
                    if not locked:
                        raise CtripReviewDetailError("同渠道正在同步或回复")
                    expected_hotel, pending = collect_channel(client, channel, expected_hotel)
                    count = replace_snapshot(db, cursor, hotel_id, channel, expected_hotel, pending)
                    print(f"携程待回复 {channel}: 已刷新 {count} 条", flush=True)
            except Exception as exc:
                failed = True
                error = str(exc) if isinstance(exc, CtripReviewDetailError) else type(exc).__name__
                print(f"携程待回复 {channel}: 同步失败：{error}", flush=True)
    return int(failed)


def main():
    if not INTERNAL_HOTEL_ID or not COOKIE:
        raise CtripReviewDetailError("请配置酒店 ID 并登录携程")
    client = CtripReviewDetailClient(COOKIE)
    try:
        return sync_pending_reviews(client, INTERNAL_HOTEL_ID, HOTEL_ID)
    finally:
        client.session.close()


if __name__ == "__main__":
    raise SystemExit(main())
