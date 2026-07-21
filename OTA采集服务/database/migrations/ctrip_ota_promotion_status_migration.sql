CREATE TABLE IF NOT EXISTS `ctrip_ota_promotion_status` (
    `hotel_id` VARCHAR(64) NOT NULL,
    `hotel_name` VARCHAR(255) NOT NULL,
    `platform_scope` VARCHAR(20) NOT NULL,
    `activity_code` VARCHAR(64) NOT NULL,
    `activity_name` VARCHAR(64) NOT NULL,
    `enabled` TINYINT NULL,
    `metric_value` DECIMAL(10,2) NULL,
    `metric_unit` VARCHAR(20) NULL,
    `status` VARCHAR(32) NOT NULL,
    `status_detail` VARCHAR(64) NULL,
    `room_type_count` INT UNSIGNED NULL,
    `orders_30d` INT UNSIGNED NULL,
    `snapshot_time` DATETIME NOT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`hotel_id`, `platform_scope`, `activity_code`),
    KEY `idx_ctrip_promotion_status_snapshot` (`hotel_id`, `snapshot_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Ctrip current promotion and service participation status by activity';
