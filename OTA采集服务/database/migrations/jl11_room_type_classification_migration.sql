CREATE TABLE IF NOT EXISTS `jl11_room_type_classification` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `hotel_id` VARCHAR(64) NOT NULL,
    `hotel_name` VARCHAR(255) NOT NULL,
    `source_platform` VARCHAR(32) NOT NULL DEFAULT 'PMS',
    `snapshot_date` DATE NOT NULL,
    `period_start` DATE NOT NULL,
    `period_end` DATE NOT NULL,
    `section` VARCHAR(16) NOT NULL,
    `room_type_id` VARCHAR(64) NULL,
    `room_type_name` VARCHAR(255) NOT NULL,
    `dimension_code` VARCHAR(64) NOT NULL DEFAULT '',
    `dimension_name` VARCHAR(128) NOT NULL DEFAULT '',
    `room_count` DECIMAL(18,4) NULL,
    `room_nights` DECIMAL(18,4) NULL,
    `occupancy_rate` DECIMAL(9,4) NULL,
    `room_revenue` DECIMAL(18,4) NULL,
    `average_room_price` DECIMAL(18,4) NULL,
    `revpar` DECIMAL(18,4) NULL,
    `overnight_room_count` DECIMAL(18,4) NULL,
    `overnight_occupancy_rate` DECIMAL(9,4) NULL,
    `snapshot_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_jl11_snapshot_row` (
        `hotel_id`, `snapshot_date`, `section`, `room_type_name`, `dimension_code`
    ),
    KEY `idx_jl11_period` (`hotel_id`, `period_end`),
    KEY `idx_jl11_room_type` (`hotel_id`, `room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
