CREATE TABLE IF NOT EXISTS `ctrip_ota_business_metrics` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `snapshot_time` DATETIME NULL,
    `business_date` DATETIME NOT NULL,
    `metric_code` VARCHAR(64) NOT NULL,
    `hotel_name` VARCHAR(255) NULL,
    `hotel_id` VARCHAR(100) NOT NULL,
    `platform_scope` VARCHAR(20) NOT NULL DEFAULT 'ctrip',
    `metric_group` VARCHAR(100) NULL,
    `metric_name` VARCHAR(100) NULL,
    `metric_value` DECIMAL(18,4) NULL,
    `metric_unit` VARCHAR(50) NULL,
    `compare_label` VARCHAR(100) NULL,
    `compare_value` VARCHAR(100) NULL,
    `competitor_rank` VARCHAR(100) NULL,
    `peer_average` VARCHAR(100) NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ctrip_business_metric_daily` (
        `hotel_id`, `platform_scope`, `business_date`, `metric_code`
    ),
    KEY `idx_ctrip_business_snapshot` (`snapshot_time`),
    KEY `idx_ctrip_business_metric` (`hotel_name`, `metric_name`),
    KEY `idx_ctrip_business_date` (`hotel_id`, `business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ctrip_ota_review_overview` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `snapshot_time` DATETIME NULL,
    `channel_source` VARCHAR(50) NULL,
    `platform_scope` VARCHAR(20) NOT NULL DEFAULT 'ctrip',
    `hotel_id` VARCHAR(100) NULL,
    `review_score` DECIMAL(10,2) NULL,
    `review_score_max` DECIMAL(10,2) NULL,
    `environment_score` DECIMAL(10,2) NULL,
    `facility_score` DECIMAL(10,2) NULL,
    `style_score` DECIMAL(10,2) NULL,
    `safety_score` DECIMAL(10,2) NULL,
    `service_score` DECIMAL(10,2) NULL,
    `hygiene_score` DECIMAL(10,2) NULL,
    `total_review_count` INT NULL,
    `unreplied_review_count` INT NULL,
    `negative_review_count` INT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_ctrip_review_overview_snapshot` (`snapshot_time`, `channel_source`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ctrip_ota_review_ranking` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `snapshot_time` DATETIME NULL,
    `channel_source` VARCHAR(50) NULL,
    `platform_scope` VARCHAR(20) NOT NULL DEFAULT 'ctrip',
    `hotel_id` VARCHAR(100) NULL,
    `ranking_type` VARCHAR(100) NULL,
    `ranking_position` INT NULL,
    `rank_item_name` VARCHAR(255) NULL,
    `rank_item_value` DECIMAL(18,4) NULL,
    PRIMARY KEY (`id`),
    KEY `idx_ctrip_review_ranking_snapshot` (
        `snapshot_time`, `channel_source`, `ranking_type`
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ctrip_ota_review_detail` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `snapshot_time` DATETIME NULL,
    `channel_source` VARCHAR(50) NULL,
    `platform_scope` VARCHAR(20) NOT NULL DEFAULT 'ctrip',
    `hotel_id` VARCHAR(100) NULL,
    `hotel_name` VARCHAR(255) NULL,
    `poi_id` VARCHAR(100) NOT NULL,
    `review_id` VARCHAR(100) NOT NULL,
    `reviewer_name_masked` VARCHAR(100) NULL,
    `review_score` DECIMAL(10,2) NULL,
    `review_content` TEXT NULL,
    `review_time` DATETIME NULL,
    `stay_date` DATE NULL,
    `merchant_reply_content` TEXT NULL,
    `merchant_reply_time` DATETIME NULL,
    `is_replied` TINYINT(1) NULL,
    `room_type_name` TEXT NULL,
    `room_type_id` VARCHAR(50) NULL,
    `ota_product_name` TEXT NULL,
    `has_image` TINYINT(1) NULL,
    `image_count` INT NULL,
    `image_urls_json` LONGTEXT NULL,
    `is_anonymous` TINYINT(1) NULL,
    `is_negative_review` TINYINT(1) NULL,
    `read_status` INT NULL,
    `hygiene_score` DECIMAL(10,2) NULL,
    `facility_score` DECIMAL(10,2) NULL,
    `location_score` DECIMAL(10,2) NULL,
    `service_score` DECIMAL(10,2) NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ctrip_review_detail` (
        `channel_source`, `platform_scope`, `poi_id`, `review_id`
    ),
    KEY `idx_ctrip_review_detail_time` (`review_time`),
    KEY `idx_ctrip_review_detail_status` (`is_negative_review`, `is_replied`),
    KEY `idx_ctrip_review_room_type` (`hotel_id`, `room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ctrip_ota_promotion_activity` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `snapshot_time` DATETIME NULL,
    `channel_source` VARCHAR(50) NULL,
    `platform_scope` VARCHAR(20) NOT NULL DEFAULT 'ctrip',
    `hotel_id` VARCHAR(100) NULL,
    `activity_source_type` VARCHAR(100) NULL,
    `activity_name` VARCHAR(255) NULL,
    `activity_status` VARCHAR(100) NULL,
    `activity_time_range` VARCHAR(255) NULL,
    `activity_rule_labels` TEXT NULL,
    `activity_room_type_summary` TEXT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_ctrip_promotion_snapshot` (`snapshot_time`, `channel_source`),
    KEY `idx_ctrip_promotion_activity` (`activity_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ctrip_ota_activity_product_detail` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `snapshot_time` DATETIME NULL,
    `channel_source` VARCHAR(50) NULL,
    `platform_scope` VARCHAR(20) NOT NULL DEFAULT 'ctrip',
    `hotel_id` VARCHAR(100) NULL,
    `activity_source_type` VARCHAR(100) NULL,
    `activity_name` VARCHAR(255) NULL,
    `ota_room_type_id` VARCHAR(100) NULL,
    `room_type_name` VARCHAR(255) NULL,
    `room_type_id` VARCHAR(50) NULL,
    `remaining_inventory` VARCHAR(100) NULL,
    PRIMARY KEY (`id`),
    KEY `idx_ctrip_product_snapshot` (`snapshot_time`, `channel_source`),
    KEY `idx_ctrip_product_activity` (`activity_name`),
    KEY `idx_ctrip_product_room` (`ota_room_type_id`, `room_type_name`),
    KEY `idx_ctrip_product_room_type` (`hotel_id`, `room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ctrip_ota_goods_price_mapping` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `snapshot_time` DATETIME NULL,
    `channel_source` VARCHAR(50) NULL,
    `platform_scope` VARCHAR(20) NOT NULL DEFAULT 'ctrip',
    `hotel_id` VARCHAR(100) NULL,
    `ota_hotel_id` VARCHAR(100) NULL,
    `ota_room_type_id` VARCHAR(100) NULL,
    `room_type_name` VARCHAR(255) NULL,
    `room_type_id` VARCHAR(50) NULL,
    `business_date` DATE NULL,
    `ota_product_id` VARCHAR(100) NULL,
    `ota_product_name` VARCHAR(500) NULL,
    `product_cipher` TEXT NULL,
    `price_editable_flag` TINYINT(1) NULL,
    `is_hour_room` TINYINT(1) NULL,
    `ota_sale_price` DECIMAL(10,2) NULL,
    `commission_rate` VARCHAR(20) NULL,
    PRIMARY KEY (`id`),
    KEY `idx_ctrip_goods_price_snapshot` (`snapshot_time`, `channel_source`),
    KEY `idx_ctrip_goods_price_business_date` (`business_date`),
    KEY `idx_ctrip_goods_price_product` (`ota_product_id`),
    KEY `idx_ctrip_goods_price_room` (`ota_room_type_id`, `room_type_name`),
    KEY `idx_ctrip_goods_price_room_type` (`hotel_id`, `room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
