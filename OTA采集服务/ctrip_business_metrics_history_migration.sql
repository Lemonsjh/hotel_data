-- 携程经营指标使用与美团经营一致的核心字段，额外保留指标分组，并保留近30天历史。
-- 执行前会清理无效行和重复行，重复指标保留最新写入的一条。
DELETE FROM ctrip_ota_business_metrics
WHERE hotel_id IS NULL OR hotel_id = ''
   OR business_date IS NULL
   OR stats_period_type IS NULL OR stats_period_type = ''
   OR metric_name IS NULL OR metric_name = '';

UPDATE ctrip_ota_business_metrics
SET metric_name = CASE metric_name
    WHEN 'realtime_booking_order_count' THEN 'booking_order_count'
    WHEN 'realtime_inhouse_room_night' THEN 'inhouse_room_night'
    ELSE metric_name
END;

DELETE FROM ctrip_ota_business_metrics
WHERE metric_name IN (
    'lost_room_night_count_7d',
    'lost_visitor_count_7d',
    'lost_order_amount_7d'
);

DELETE FROM ctrip_ota_business_metrics
WHERE metric_name IN (
    'order_page_visitor_count',
    'transaction_conversion_rate',
    'visitor_order_conversion_rate',
    'detail_page_uv_count',
    'detail_page_order_count',
    'checkout_sales_amount',
    'checkout_room_night',
    'checkout_conversion_rate',
    'checkout_average_sale_price',
    'realtime_ctrip_order_count',
    'realtime_qunar_order_count',
    'realtime_elong_order_count',
    'realtime_visitor_count',
    'realtime_rank',
    'qunar_realtime_visitor_count',
    'lowest_sale_price'
);

DELETE older
FROM ctrip_ota_business_metrics AS older
JOIN ctrip_ota_business_metrics AS newer
  ON older.id < newer.id
 AND older.hotel_id = newer.hotel_id
 AND DATE(older.business_date) = DATE(newer.business_date)
 AND older.metric_name = newer.metric_name;

ALTER TABLE ctrip_ota_business_metrics
  DROP INDEX uk_ctrip_business_metric_daily,
  DROP INDEX idx_ctrip_business_metric,
  DROP INDEX idx_ctrip_business_date,
  MODIFY business_date DATETIME NOT NULL AFTER snapshot_time,
  CHANGE metric_name metric_code VARCHAR(64) NOT NULL AFTER business_date,
  MODIFY hotel_name VARCHAR(255) NULL AFTER metric_code,
  MODIFY hotel_id VARCHAR(100) NOT NULL AFTER hotel_name,
  MODIFY metric_group VARCHAR(100) NULL AFTER hotel_id,
  CHANGE metric_display_name metric_name VARCHAR(100) NULL AFTER metric_group,
  DROP COLUMN stats_period_type,
  DROP COLUMN period_days,
  ADD UNIQUE KEY uk_ctrip_business_metric_daily (
    hotel_id, business_date, metric_code
  ),
  ADD KEY idx_ctrip_business_metric (hotel_name, metric_name),
  ADD KEY idx_ctrip_business_date (hotel_id, business_date);
