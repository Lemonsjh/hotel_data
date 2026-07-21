CREATE TABLE IF NOT EXISTS `ctrip_ota_competition_metrics_30d` (
    `hotel_id` VARCHAR(64) NOT NULL,
    `hotel_name` VARCHAR(255) NOT NULL,
    `platform_scope` VARCHAR(20) NOT NULL DEFAULT 'ctrip',
    `metric_code` VARCHAR(64) NOT NULL,
    `metric_name` VARCHAR(100) NOT NULL,
    `metric_unit` VARCHAR(20) NULL,
    `period_start_date` DATE NOT NULL,
    `period_end_date` DATE NOT NULL,
    `snapshot_time` DATETIME NOT NULL,
    `hotel_value` DECIMAL(18,4) NULL,
    `previous_value` DECIMAL(18,4) NULL,
    `competitor_avg` DECIMAL(18,4) NULL,
    `competitor_rank` INT NULL,
    `previous_rank` INT NULL,
    PRIMARY KEY (`hotel_id`, `platform_scope`, `metric_code`),
    KEY `idx_ctrip_competition_period_end` (`hotel_id`, `period_end_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
