"""Send explicitly queued Ctrip review replies and verify them against the review list."""
from __future__ import annotations

import argparse
import os
import time

from ctrip_review_detail_data import CHANNELS, COOKIE, TABLE_NAME, CtripReviewDetailClient, build_payload, merchant_reply, to_datetime
from ota_mysql_writer import connect_mysql
from update_guard import ensure_not_updating

TASK_TABLE = "ota_review_reply_task"
REPLY_URL = "https://ebooking.ctrip.com/restapi/soa2/26353/setCommentReply"
CHANNEL_KEYS = {label: key for key, (_, label) in CHANNELS.items()} | {key: key for key in CHANNELS}


def find_review(client, channel, review_id, poi_id):
    source, _ = CHANNELS[channel]
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
            or str((result.get("resStatus") or {}).get("rcode")) != "200"
            or str(result.get("replystatus")) != "1"):
        raise ValueError("携程接口未确认回复成功")


def execute_reply(client, channel, review_id, poi_id, content):
    review = find_review(client, channel, review_id, poi_id)
    existing = merchant_reply(review)
    if str(existing.get("replyContent") or "").strip() != content:
        if (str(existing.get("replyId") or "") not in {"", "0", "-1"}
                or existing.get("replyContent")):
            raise ValueError("该评论已有其他回复")
        if review.get("enableReply") is not True:
            raise ValueError("携程当前不允许回复该评论")
        submit_reply(client, review_id, CHANNELS[channel][0], content)
        existing = merchant_reply(find_review(client, channel, review_id, poi_id))
        if str(existing.get("replyContent") or "").strip() != content:
            raise ValueError("提交后未查到对应回复，请在携程后台核对")
    return to_datetime(existing.get("replyTime"))


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
            for item_id, label, review_id, content in tasks:
                cursor.execute(f"UPDATE `{TASK_TABLE}` SET status='processing',error_message=NULL "
                               "WHERE id=%s AND status='pending'", (item_id,))
                if not cursor.rowcount:
                    continue
                try:
                    channel = CHANNEL_KEYS.get(label)
                    content = str(content or "").strip()
                    if not channel or not review_id or not content:
                        raise ValueError("评价渠道、评论 ID 或回复内容无效")
                    cursor.execute(f"SELECT id,poi_id FROM `{TABLE_NAME}` WHERE hotel_id=%s "
                                   "AND platform_scope=%s AND review_id=%s LIMIT 2", (hotel_id, channel, review_id))
                    matches = cursor.fetchall()
                    if len(matches) != 1 or not matches[0][1]:
                        raise ValueError("本地评价无法唯一匹配到携程门店")
                    local_id, poi_id = matches[0]
                    replied_at = execute_reply(client, channel, str(review_id), str(poi_id), content)
                    db.begin()
                    cursor.execute(f"UPDATE `{TABLE_NAME}` SET is_replied=1,merchant_reply_content=%s,"
                                   "merchant_reply_time=%s WHERE id=%s AND hotel_id=%s", (content, replied_at, local_id, hotel_id))
                    cursor.execute(f"UPDATE `{TASK_TABLE}` SET status='success',replied_at=COALESCE(%s,NOW()) "
                                   "WHERE id=%s AND status='processing'", (replied_at, item_id))
                    db.commit()
                    print(f"Ctrip reply task {item_id} completed")
                except Exception as exc:
                    db.rollback()
                    failed = True
                    reason = str(exc) if type(exc) is ValueError else f"携程回复未确认（{type(exc).__name__}），请在后台核对"
                    cursor.execute(f"UPDATE `{TASK_TABLE}` SET status='failed',error_message=%s "
                                   "WHERE id=%s AND status='processing'", (reason[:500], item_id))
                    print(f"Ctrip reply task {item_id} failed: {reason}")
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
