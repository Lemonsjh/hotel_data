-- 删除 KF11 完全相同的重复状态，保留最早写入的一条。
-- snapshot_time 不参与比较；同一房间真实发生的状态变化会保留。
DELETE newer
FROM kf11_room_status_snapshot AS newer
JOIN kf11_room_status_snapshot AS older
  ON newer.id > older.id
 AND newer.hotel_name <=> older.hotel_name
 AND newer.hotel_id <=> older.hotel_id
 AND newer.source_platform <=> older.source_platform
 AND newer.business_date <=> older.business_date
 AND newer.room_no <=> older.room_no
 AND newer.room_type_name <=> older.room_type_name
 AND newer.room_status <=> older.room_status
 AND newer.guest_name <=> older.guest_name
 AND newer.checkin_time <=> older.checkin_time
 AND newer.checkout_time <=> older.checkout_time;
