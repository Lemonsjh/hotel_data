CREATE TABLE IF NOT EXISTS `meituan_ota_business_metrics` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `snapshot_time` DATETIME NULL,
    `business_date` DATETIME NULL,
    `metric_code` VARCHAR(64) NULL,
    `hotel_name` VARCHAR(255) NULL,
    `hotel_id` VARCHAR(100) NULL,
    `metric_name` VARCHAR(100) NULL,
    `metric_value` DECIMAL(18,4) NULL,
    `metric_unit` VARCHAR(50) NULL,
    `compare_label` VARCHAR(100) NULL,
    `compare_value` VARCHAR(100) NULL,
    `competitor_rank` VARCHAR(100) NULL,
    `peer_average` VARCHAR(100) NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_meituan_business_metric_daily` (
        `hotel_id`, `business_date`, `metric_code`
    ),
    KEY `idx_meituan_ota_business_snapshot` (`snapshot_time`),
    KEY `idx_meituan_ota_business_metric` (`hotel_name`, `metric_name`),
    KEY `idx_meituan_business_date` (`hotel_id`, `business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `meituan_ota_review_overview` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `snapshot_time` DATETIME NULL,
    `channel_source` VARCHAR(50) NULL,
    `review_platform` VARCHAR(20) NOT NULL DEFAULT 'meituan',
    `hotel_id` VARCHAR(100) NULL,
    `review_score` DECIMAL(10,2) NULL,
    `review_score_max` DECIMAL(10,2) NULL,
    `environment_score` DECIMAL(10,2) NULL,
    `facility_score` DECIMAL(10,2) NULL,
    `service_score` DECIMAL(10,2) NULL,
    `hygiene_score` DECIMAL(10,2) NULL,
    `total_review_count` INT NULL,
    `unreplied_review_count` INT NULL,
    `negative_review_count` INT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_meituan_ota_review_overview_snapshot` (`snapshot_time`, `channel_source`),
    KEY `idx_meituan_review_overview_platform` (
        `hotel_id`, `review_platform`, `snapshot_time`
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
