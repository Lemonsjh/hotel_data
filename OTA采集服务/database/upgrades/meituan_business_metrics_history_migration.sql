ALTER TABLE `meituan_ota_business_metrics`
    ADD COLUMN `metric_code` VARCHAR(64) NULL COMMENT '指标内部编码' AFTER `business_date`;

ALTER TABLE `meituan_ota_business_metrics`
    DROP COLUMN `stats_period_type`,
    DROP COLUMN `period_days`,
    ADD UNIQUE KEY `uk_meituan_business_metric_daily` (`hotel_id`, `business_date`, `metric_code`),
    ADD KEY `idx_meituan_business_date` (`hotel_id`, `business_date`);
