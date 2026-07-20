CREATE TABLE IF NOT EXISTS `ctrip_ota_order_loss_monthly` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `hotel_id` VARCHAR(64) NOT NULL,
    `hotel_name` VARCHAR(255) NOT NULL,
    `platform_scope` VARCHAR(20) NOT NULL,
    `business_date` DATE NOT NULL,
    `period_start_date` DATE NOT NULL,
    `period_end_date` DATE NOT NULL,
    `snapshot_time` DATETIME NOT NULL,
    `ranking_position` INT NOT NULL,
    `competitor_hotel_name` VARCHAR(255) NOT NULL,
    `common_browse_rate_pct` DECIMAL(9,4) NULL,
    `order_conversion_rate_pct` DECIMAL(9,4) NULL,
    `loss_order_count` INT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ctrip_order_loss_monthly` (
        `hotel_id`, `platform_scope`, `period_end_date`, `ranking_position`
    ),
    KEY `idx_ctrip_order_loss_period` (`hotel_id`, `period_end_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
