-- 区分携程后台中的携程、去哪儿、同程旅行数据口径。
ALTER TABLE ctrip_ota_business_metrics
  ADD COLUMN platform_scope VARCHAR(20) NOT NULL DEFAULT 'ctrip' AFTER hotel_id;
ALTER TABLE ctrip_ota_review_overview
  ADD COLUMN platform_scope VARCHAR(20) NOT NULL DEFAULT 'ctrip' AFTER channel_source;
ALTER TABLE ctrip_ota_review_ranking
  ADD COLUMN platform_scope VARCHAR(20) NOT NULL DEFAULT 'ctrip' AFTER channel_source;
ALTER TABLE ctrip_ota_review_detail
  ADD COLUMN platform_scope VARCHAR(20) NOT NULL DEFAULT 'ctrip' AFTER channel_source;
ALTER TABLE ctrip_ota_promotion_activity
  ADD COLUMN platform_scope VARCHAR(20) NOT NULL DEFAULT 'ctrip' AFTER channel_source;
ALTER TABLE ctrip_ota_activity_product_detail
  ADD COLUMN platform_scope VARCHAR(20) NOT NULL DEFAULT 'ctrip' AFTER channel_source;
ALTER TABLE ctrip_ota_goods_price_mapping
  ADD COLUMN platform_scope VARCHAR(20) NOT NULL DEFAULT 'ctrip' AFTER channel_source;

ALTER TABLE ctrip_ota_business_metrics
  DROP INDEX uk_ctrip_business_metric_daily,
  ADD UNIQUE KEY uk_ctrip_business_metric_daily (
    hotel_id, platform_scope, business_date, metric_code
  );
ALTER TABLE ctrip_ota_review_detail
  DROP INDEX uk_ctrip_review_detail,
  ADD UNIQUE KEY uk_ctrip_review_detail (
    channel_source, platform_scope, poi_id, review_id
  );
