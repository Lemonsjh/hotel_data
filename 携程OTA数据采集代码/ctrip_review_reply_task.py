"""Send queued Ctrip review replies and record confirmed submissions."""
from __future__ import annotations

import argparse
import os
import time
from contextlib import nullcontext
from datetime import datetime

from ctrip_review_detail_data import CHANNELS, COOKIE, TABLE_NAME, CtripReviewDetailClient, build_payload, merchant_reply, to_datetime
from ctrip_pending_reviews import PENDING_TABLE, TASK_TABLE, channel_lock, pending_pages, table_exists
from ota_mysql_writer import connect_mysql
from update_guard import ensure_not_updating

REPLY_URL = "https://ebooking.ctrip.com/restapi/soa2/26353/setCommentReply"
CHANNEL_KEYS = {label: key for key, (_, label) in CHANNELS.items()} | {key: key for key in CHANNELS}


def find_review(client, channel, review_id, poi_id, *, prefer_pending=False):
    source, _ = CHANNELS[channel]
    if prefer_pending:
        for _, reviews in pending_pages(client, source, poi_id):
            for review in reviews:
                if str(review.get("commentId")) == review_id:
                    return review
    for page in range(1, 21):
        payload = client.fetch_page(page, 30, source)
        reviews = payload.get("commentlist")
        if not isinstance(reviews, list) or str(payload.get("masterHotelId")) != poi_id:
            raise ValueError("携程评价列表未确认当前酒店")
        for review in reviews:
            if str(review.get("commentId")) == review_id:
                if str(review.get("sourceId")) != source:
                    raise ValueError("携程评价渠道不符")
                return review
        if not reviews or page >= int(payload.get("pageCount") or page):
            break
        time.sleep(0.2)
    raise ValueError("携程评价列表未找到该评论")


def submit_reply(client, review_id, source, content):
    page = build_payload(1, 30, source)
    client_id = page["reqHead"]["client"]["clientId"]
    response = client.session.post(REPLY_URL, params={"_fxpcqlniredt": client_id}, json={
        "reqHead": page["reqHead"],
        "head": {"cid": client_id, "ctok": "", "cver": "1.0", "lang": "01", "sid": "8888",
                 "syscode": "09", "auth": "", "xsid": "", "extension": []},
        "header": page["header"], "terminal": "pc",
        "commentid": review_id, "sourceid": int(source), "fbcommentid": -1, "content": content,
    }, timeout=30, allow_redirects=False)
    if response.status_code != 200:
        raise ValueError(f"携程回复接口 HTTP {response.status_code}")
    result = response.json()
    if (not isinstance(result, dict) or (result.get("ResponseStatus") or {}).get("Ack") != "Success"
            or (result.get("ResponseStatus") or {}).get("Errors")
            or str((result.get("resStatus") or {}).get("rcode")) != "200"):
        raise ValueError("携程接口未确认回复成功")
    qunar_ack_only = source == CHANNELS["qunar"][0] and "replystatus" not in result
    if str(result.get("replystatus")) != "1" and not qunar_ack_only:
        raise ValueError("携程接口未确认回复成功")


def execute_reply(client, channel, review_id, poi_id, content, *, prefer_pending=False):
    review = find_review(client, channel, review_id, poi_id, prefer_pending=prefer_pending)
    existing = merchant_reply(review)
    if str(existing.get("replyContent") or "").strip() == content:
        return to_datetime(existing.get("replyTime"))
    if str(existing.get("replyId") or "") not in {"", "0", "-1"} or existing.get("replyContent"):
        raise ValueError("该评论已有其他回复")
    if review.get("enableReply") is not True:
        raise ValueError("携程当前不允许回复该评论")
    submit_reply(client, review_id, CHANNELS[channel][0], content)
    return datetime.now().replace(microsecond=0)


def resolve_review(cursor, hotel_id, channel, review_id, has_snapshot):
    scope = (hotel_id, channel, review_id)
    pending = None
    if has_snapshot:
        cursor.execute(f"SELECT poi_id FROM `{PENDING_TABLE}` "
                       "WHERE hotel_id=%s AND platform_scope=%s AND review_id=%s", scope)
        pending = cursor.fetchone()
    conditions = "hotel_id=%s AND platform_scope=%s AND review_id=%s"
    if pending:
        conditions += " AND poi_id=%s"
    cursor.execute(f"SELECT id,poi_id FROM `{TABLE_NAME}` WHERE {conditions} LIMIT 2", (*scope, *pending) if pending else scope)
    matches = cursor.fetchall()
    if pending and not matches:
        return None, pending[0], True
    if len(matches) != 1 or not matches[0][1]:
        raise ValueError("本地评价无法唯一匹配到携程门店")
    local_id, poi_id = matches[0]
    return local_id, poi_id, bool(pending)


def process_task(client, db, cursor, hotel_id, task):
    item_id, label, review_id, content = task
    channel = CHANNEL_KEYS.get(label)
    lock = channel_lock(cursor, hotel_id, channel) if channel else nullcontext(True)
    with lock as locked:
        if not locked:
            print(f"Ctrip reply task {item_id} deferred: channel synchronization in progress")
            return True
        cursor.execute(f"UPDATE `{TASK_TABLE}` SET status='processing',error_message=NULL "
                       "WHERE id=%s AND status='pending'", (item_id,))
        if not cursor.rowcount:
            return True
        try:
            content = str(content or "").strip()
            if not channel or not review_id or not content:
                raise ValueError("评价渠道、评论 ID 或回复内容无效")
            has_snapshot = table_exists(cursor, PENDING_TABLE)
            local_id, poi_id, prefer_pending = resolve_review(cursor, hotel_id, channel, review_id, has_snapshot)
            replied_at = execute_reply(client, channel, str(review_id), str(poi_id), content,
                                       prefer_pending=prefer_pending)
            db.begin()
            if local_id is not None:
                cursor.execute(f"UPDATE `{TABLE_NAME}` SET is_replied=1,merchant_reply_content=%s,"
                               "merchant_reply_time=%s WHERE id=%s AND hotel_id=%s", (content, replied_at, local_id, hotel_id))
            if has_snapshot:
                cursor.execute(f"DELETE FROM `{PENDING_TABLE}` "
                               "WHERE hotel_id=%s AND platform_scope=%s AND review_id=%s AND poi_id=%s",
                               (hotel_id, channel, review_id, poi_id))
            cursor.execute(f"UPDATE `{TASK_TABLE}` SET status='success',replied_at=COALESCE(%s,NOW()) "
                           "WHERE id=%s AND status='processing'", (replied_at, item_id))
            db.commit()
            print(f"Ctrip reply task {item_id} completed")
            return True
        except Exception as exc:
            db.rollback()
            reason = str(exc) if type(exc) is ValueError else f"携程回复未确认（{type(exc).__name__}），请在后台核对"
            cursor.execute(f"UPDATE `{TASK_TABLE}` SET status='failed',error_message=%s "
                           "WHERE id=%s AND status='processing'", (reason[:500], item_id))
            print(f"Ctrip reply task {item_id} failed: {reason}")
            return False


def run_tasks(hotel_id, limit, task_id=None):
    if not hotel_id:
        raise ValueError("请配置酒店 ID")
    with connect_mysql(autocommit=True) as db, db.cursor() as cursor:
        cursor.execute(f"""SELECT id,channel_source,review_id,reply_content FROM `{TASK_TABLE}`
            WHERE hotel_id=%s AND platform='ctrip' AND status='pending' AND (%s IS NULL OR id=%s)
            ORDER BY id LIMIT %s""", (hotel_id, task_id, task_id, limit))
        tasks = cursor.fetchall()
    if not tasks:
        print("No pending Ctrip review reply tasks")
        return 0
    if not COOKIE:
        raise ValueError("请先登录携程")
    client = CtripReviewDetailClient(COOKIE)
    failed = False
    try:
        with connect_mysql(autocommit=True) as db, db.cursor() as cursor:
            for task in tasks:
                failed |= not process_task(client, db, cursor, hotel_id, task)
    finally:
        client.session.close()
    return int(failed)


def main():
    ensure_not_updating()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-tasks", type=int, default=10)
    parser.add_argument("--task-id", type=int)
    args = parser.parse_args()
    return run_tasks(os.environ.get("HOTEL_ID", "").strip(), max(1, args.max_tasks), args.task_id)


if __name__ == "__main__":
    raise SystemExit(main())
