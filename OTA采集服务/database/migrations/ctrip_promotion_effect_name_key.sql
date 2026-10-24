-- Run in the intended hotel database before deploying the new promotion-effect collector.
-- Preflight: the following queries must return no rows. Resolve conflicts explicitly;
-- do not automatically delete, merge or rename historical promotions.
SELECT hotel_id, platform_scope, campaign_name
FROM ctrip_ota_promotion_activity_performance
WHERE campaign_name IS NULL OR TRIM(campaign_name) = '';

SELECT hotel_id, platform_scope, campaign_name, COUNT(*) AS row_count
FROM ctrip_ota_promotion_activity_performance
GROUP BY hotel_id, platform_scope, campaign_name
HAVING COUNT(*) > 1;

-- No business rows are deleted or updated. ALTER fails if the new key cannot be built.
ALTER TABLE ctrip_ota_promotion_activity_performance
  DROP PRIMARY KEY,
  MODIFY COLUMN campaign_id BIGINT UNSIGNED NULL DEFAULT NULL,
  MODIFY COLUMN campaign_name VARCHAR(255) NOT NULL,
  ADD PRIMARY KEY (hotel_id, platform_scope, campaign_name);
