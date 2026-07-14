ALTER TABLE rs01_room_revenue_daily
  MODIFY order_id VARCHAR(100) NOT NULL DEFAULT '',
  MODIFY charge_subject VARCHAR(100) NOT NULL DEFAULT '',
  DROP INDEX uk_room_fee_daily,
  ADD UNIQUE KEY uk_room_fee_daily (
    hotel_id, order_id, business_date, room_no, charge_subject
  );
