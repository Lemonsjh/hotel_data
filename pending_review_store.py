from contextlib import contextmanager
from hashlib import sha256

from ota_mysql_writer import DB_CONFIG

TASK_TABLE = "ota_review_reply_task"


@contextmanager
def channel_lock(cursor, hotel_id, channel, *, timeout=0):
    scope = f"{DB_CONFIG['database']}\0{hotel_id}\0{channel}".encode()
    name = "ota_pending:" + sha256(scope).hexdigest()[:48]
    cursor.execute("SELECT GET_LOCK(%s, %s)", (name, timeout))
    acquired = cursor.fetchone() == (1,)
    try:
        yield acquired
    finally:
        if acquired:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (name,))


def table_exists(cursor, table):
    cursor.execute("SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                   "AND TABLE_NAME=%s", (table,))
    return cursor.fetchone() is not None


def replace_channel_snapshot(db, cursor, table, platform, hotel_id, channel, label, poi_id, pending):
    scope = (hotel_id, channel)
    db.begin()
    try:
        if pending and table_exists(cursor, TASK_TABLE):
            # A platform response can briefly lag a reply that it has already acknowledged.
            ids = [row[0] for row in pending]
            marks = ",".join(["%s"] * len(ids))
            cursor.execute(f"SELECT review_id FROM `{TASK_TABLE}` WHERE hotel_id=%s AND platform=%s "
                           "AND channel_source IN (%s,%s) AND status='success' "
                           f"AND review_id IN ({marks})", (hotel_id, platform, channel, label, *ids))
            replied = {str(row[0]) for row in cursor.fetchall()}
            pending = [row for row in pending if row[0] not in replied]
        cursor.execute(f"DELETE FROM `{table}` WHERE hotel_id=%s AND platform_scope=%s", scope)
        if pending:
            cursor.executemany(f"INSERT INTO `{table}` (hotel_id,platform_scope,channel_source,poi_id,"
                               "review_id,review_time,reviewer_name_masked,review_score,review_content,room_type_name,"
                               "enable_reply,snapshot_time) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                               [(*scope, label, poi_id, *row) for row in pending])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(pending)
