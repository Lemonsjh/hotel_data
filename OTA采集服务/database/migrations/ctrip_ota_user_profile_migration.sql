CREATE TABLE IF NOT EXISTS `ctrip_ota_userprofile_distribution` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `hotel_id` VARCHAR(64) NOT NULL,
    `hotel_name` VARCHAR(255) NOT NULL,
    `platform_scope` VARCHAR(20) NOT NULL,
    `snapshot_time` DATETIME NOT NULL,
    `dimension_code` VARCHAR(40) NOT NULL,
    `bucket_label` VARCHAR(100) NOT NULL,
    `rate_pct` DECIMAL(9,4) NULL,
    `metric_value` DECIMAL(12,4) NULL,
    `metric_unit` VARCHAR(20) NULL,
    `rank_position` INT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ctrip_userprofile_distribution` (
        `hotel_id`, `platform_scope`, `dimension_code`, `bucket_label`
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
