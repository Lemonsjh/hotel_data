CREATE TABLE IF NOT EXISTS `ctrip_ota_business_metrics_hourly` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `hotel_id` VARCHAR(100) NOT NULL,
    `hotel_name` VARCHAR(255) NULL,
    `platform_scope` VARCHAR(20) NOT NULL DEFAULT 'ctrip',
    `business_date` DATE NOT NULL,
    `snapshot_time` DATETIME NOT NULL,
    `snapshot_hour` DATETIME NOT NULL,
    `metric_code` VARCHAR(64) NOT NULL,
    `metric_group` VARCHAR(100) NULL,
    `metric_name` VARCHAR(100) NOT NULL,
    `metric_value` DECIMAL(18,4) NULL,
    `metric_unit` VARCHAR(50) NULL,
    `compare_label` VARCHAR(100) NULL,
    `compare_value` VARCHAR(100) NULL,
    `competitor_rank` VARCHAR(100) NULL COMMENT '同行排名',
    `peer_average` VARCHAR(100) NULL COMMENT '同行均值',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ctrip_business_hourly` (
        `hotel_id`, `platform_scope`, `snapshot_hour`, `metric_code`
    ),
    KEY `idx_ctrip_business_hourly_date` (`hotel_id`, `business_date`),
    KEY `idx_ctrip_business_hourly_snapshot` (`snapshot_time`),
    KEY `idx_ctrip_business_hourly_metric` (`hotel_id`, `metric_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
