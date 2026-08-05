from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from meituan_config import BIZ_ACCOUNT_ID, PARTNER_ID, POI_ID, USER_AGENT

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import connect_mysql
from meituan_page_capture import browser_profile_lock, page_access_issue, profile_path


TASK_TABLE = "ota_review_reply_task"
REVIEW_TABLE = "meituan_ota_review_detail"
COMMENT_PAGE_URL = "https://me.meituan.com/ebooking/merchant/comment-manage-react"
REPLY_URL = "https://me.meituan.com/api/gw/v1/base/comments/{comment_id}/replyInfo"
COMMENT_LIST_URL = "https://me.meituan.com/api/gw/v1/base/comments/queryGeneralCommentInfo"
CHANNEL_PLATFORMS = {"meituan": 1, "dianping": 0}


def claim_tasks(hotel_id: str, limit: int, task_id: int | None) -> list[dict[str, Any]]:
    connection = connect_mysql(autocommit=True)
    try:
        with connection.cursor() as cursor:
            channels = tuple(CHANNEL_PLATFORMS)
            marks = ", ".join(["%s"] * len(channels))
            conditions = ["hotel_id=%s", "platform='meituan'", f"channel_source IN ({marks})", "status='pending'"]
            values: list[Any] = [hotel_id, *channels]
            if task_id is not None:
                conditions.append("id=%s")
                values.append(task_id)
            cursor.execute(
                f"SELECT id, review_id, reply_content, channel_source FROM `{TASK_TABLE}` "
                f"WHERE {' AND '.join(conditions)} ORDER BY id LIMIT %s",
                [*values, limit],
            )
            candidates = cursor.fetchall()
            claimed: list[dict[str, Any]] = []
            for item_id, review_id, reply_content, channel_source in candidates:
                cursor.execute(
                    f"UPDATE `{TASK_TABLE}` SET status='processing', error_message=NULL "
                    "WHERE id=%s AND status='pending'",
                    (item_id,),
                )
                if cursor.rowcount:
                    claimed.append({
                        "id": item_id, "review_id": str(review_id), "reply_content": str(reply_content),
                        "channel_source": str(channel_source),
                    })
        return claimed
    finally:
        connection.close()


def review_reply_state(hotel_id: str, review_id: str, channel_source: str) -> bool | None:
    connection = connect_mysql()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT is_replied FROM `{REVIEW_TABLE}` "
                "WHERE hotel_id=%s AND channel_source IN (%s, %s) AND review_id=%s LIMIT 1",
                (hotel_id, channel_source, "美团" if channel_source == "meituan" else "大众点评", review_id),
            )
            result = cursor.fetchone()
        return None if result is None else bool(result[0])
    finally:
        connection.close()


def finish_task(task_id: int, status: str, error: str | None = None) -> None:
    connection = connect_mysql(autocommit=True)
    try:
        with connection.cursor() as cursor:
            if status == "success":
                cursor.execute(
                    f"UPDATE `{TASK_TABLE}` SET status=%s, error_message=NULL, replied_at=NOW() WHERE id=%s",
                    (status, task_id),
                )
            else:
                cursor.execute(
                    f"UPDATE `{TASK_TABLE}` SET status=%s, error_message=%s WHERE id=%s",
                    (status, (error or "reply failed")[:500], task_id),
                )
    finally:
        connection.close()


def mark_review_replied(hotel_id: str, review_id: str, channel_source: str, content: str) -> None:
    """Keep the local detail record aligned only after platform confirmation."""
    connection = connect_mysql(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE `{REVIEW_TABLE}` SET is_replied=1, merchant_reply_content=%s, "
                "merchant_reply_time=NOW() WHERE hotel_id=%s "
                "AND channel_source IN (%s, %s) AND review_id=%s",
                (content, hotel_id, channel_source, "美团" if channel_source == "meituan" else "大众点评", review_id),
            )
    finally:
        connection.close()


def fail_interrupted_tasks(hotel_id: str) -> None:
    """Do not silently retry an attempt whose platform result is unknown."""
    connection = connect_mysql(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE `{TASK_TABLE}` SET status='failed', "
                "error_message='Previous reply attempt was interrupted before platform confirmation' "
                "WHERE hotel_id=%s AND platform='meituan' AND channel_source IN ('meituan', 'dianping') "
                "AND status='processing'",
                (hotel_id,),
            )
    finally:
        connection.close()


class ReplyClient:
    def __init__(self) -> None:
        self.lock = browser_profile_lock()
        self.lock.__enter__()
        self.playwright = sync_playwright().start()
        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                str(profile_path()), channel="msedge", headless=True,
                user_agent=USER_AGENT, viewport={"width": 1440, "height": 1000},
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            with self.page.expect_response(
                lambda response: COMMENT_LIST_URL in response.url and "replyType=0" in response.url,
                timeout=45_000,
            ):
                self.page.goto(COMMENT_PAGE_URL, wait_until="domcontentloaded", timeout=60_000)
            self.page.wait_for_timeout(1000)
            if issue := page_access_issue(self.page):
                raise RuntimeError(f"Meituan review page requires manual action: {issue}")
        except Exception:
            self.close()
            raise

    def platform_reply_state(self, review_id: str, review_platform: int) -> bool | None:
        for offset in range(1, 11):
            query = urlencode({
                "poiId": POI_ID, "partnerId": PARTNER_ID, "platform": review_platform,
                "tag": "", "keywords": "", "replyType": 0, "offset": offset, "limit": 10,
                "bizAccountId": BIZ_ACCOUNT_ID, "yodaReady": "h5", "csecplatform": 4,
                "csecversion": "4.2.4",
            })
            payload = self.page.evaluate(
                """async url => {
                    const response = await fetch(url, {
                        credentials: 'include',
                        headers: {
                            'X-Requested-With': 'XMLHttpRequest',
                            'Request-Page-Source': 'ME'
                        }
                    });
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    return await response.json();
                }""",
                f"{COMMENT_LIST_URL}?{query}",
            )
            if payload.get("code") != 10000:
                raise RuntimeError(f"Review confirmation failed: code={payload.get('code')}")
            comments = (payload.get("data") or {}).get("commentList") or []
            for item in comments:
                if str(item.get("id") or "") == review_id:
                    return bool(item.get("replyId") or str(item.get("bizReply") or "").strip())
            if len(comments) < 10:
                break
        return None

    def reply(self, review_id: str, content: str, review_platform: int) -> None:
        if not review_id.isdigit():
            raise ValueError("review_id must be numeric for Meituan replies")
        if not content.strip():
            raise ValueError("reply_content is empty")
        query = urlencode({"yodaReady": "h5", "csecplatform": 4, "csecversion": "4.2.4"})
        url = f"{REPLY_URL.format(comment_id=review_id)}?{query}"
        payload = {
            "commentId": int(review_id),
            "id": int(review_id),
            "platform": review_platform,
            "poiId": POI_ID,
            "reply": content.strip(),
            "replyId": 0,
            "userId": None,
            "bizAccountId": int(BIZ_ACCOUNT_ID),
        }
        result = self.page.evaluate(
            """async ({url, payload}) => {
                const response = await fetch(url, {
                    method: "POST",
                    credentials: "include",
                    headers: {
                        "Content-Type": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                        "Request-Page-Source": "ME"
                    },
                    body: JSON.stringify(payload)
                });
                const body = await response.json().catch(() => ({}));
                return {status: response.status, body};
            }""",
            {"url": url, "payload": payload},
        )
        code = result["body"].get("code")
        if result["status"] >= 400 or code not in (0, 10000):
            message = result["body"].get("msg") or result["body"].get("message") or f"HTTP {result['status']}"
            raise RuntimeError(f"Meituan reply failed: code={code}, message={message}")

    def close(self) -> None:
        for name in ("context", "playwright"):
            resource = getattr(self, name, None)
            if resource:
                try:
                    resource.close() if name != "playwright" else resource.stop()
                except Exception:
                    pass
        lock = getattr(self, "lock", None)
        if lock:
            try:
                lock.__exit__(None, None, None)
            except Exception:
                pass
            self.lock = None


def run(hotel_id: str, limit: int, task_id: int | None) -> int:
    if not hotel_id or not POI_ID or not BIZ_ACCOUNT_ID:
        raise RuntimeError("HOTEL_ID, MEITUAN_POI_ID and MEITUAN_BIZ_ACCOUNT_ID are required")
    fail_interrupted_tasks(hotel_id)
    tasks = claim_tasks(hotel_id, limit, task_id)
    if not tasks:
        print("No pending Meituan review reply tasks")
        return 0

    client: ReplyClient | None = None
    failed = 0
    try:
        client = ReplyClient()
        for task in tasks:
            try:
                channel_source = task["channel_source"]
                review_platform = CHANNEL_PLATFORMS.get(channel_source)
                if review_platform is None:
                    raise RuntimeError(f"Unsupported Meituan review channel: {channel_source}")
                local_state = review_reply_state(hotel_id, task["review_id"], channel_source)
                if local_state is None:
                    raise RuntimeError("Review was not found in the collected Meituan review table")
                state = client.platform_reply_state(task["review_id"], review_platform)
                if state:
                    finish_task(task["id"], "success")
                    print(f"Reply task {task['id']} already completed")
                    continue
                client.reply(task["review_id"], task["reply_content"], review_platform)
                if client.platform_reply_state(task["review_id"], review_platform) is not True:
                    raise RuntimeError("Reply request returned but platform confirmation was not found")
                mark_review_replied(hotel_id, task["review_id"], channel_source, task["reply_content"])
                finish_task(task["id"], "success")
                print(f"Reply task {task['id']} completed")
            except Exception as exc:
                failed += 1
                finish_task(task["id"], "failed", str(exc))
                print(f"Reply task {task['id']} failed: {exc}")
    finally:
        if client:
            client.close()
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Process pending Meituan review reply tasks.")
    parser.add_argument("--hotel-id", default=os.environ.get("HOTEL_ID", ""))
    parser.add_argument("--max-tasks", type=int, default=10)
    parser.add_argument("--task-id", type=int)
    args = parser.parse_args()
    return run(args.hotel_id.strip(), max(1, args.max_tasks), args.task_id)


if __name__ == "__main__":
    raise SystemExit(main())
