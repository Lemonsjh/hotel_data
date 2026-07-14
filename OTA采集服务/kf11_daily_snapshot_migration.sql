-- KF11改为每日最新快照：同一酒店、日期、房号只保留最新记录。
DELETE older
FROM kf11_room_status_snapshot AS older
JOIN kf11_room_status_snapshot AS newer
  ON older.id < newer.id
 AND older.hotel_id <=> newer.hotel_id
 AND older.source_platform <=> newer.source_platform
 AND older.business_date <=> newer.business_date
 AND older.room_no <=> newer.room_no;

ALTER TABLE kf11_room_status_snapshot
  DROP INDEX uk_room_snapshot,
  ADD UNIQUE KEY uk_kf11_daily_room (
    hotel_id,
    source_platform,
    business_date,
    room_no
  );
