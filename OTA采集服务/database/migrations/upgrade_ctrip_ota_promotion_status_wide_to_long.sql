-- Run once only for databases that already have the previous wide table.
-- The former table is retained as ctrip_ota_promotion_status_wide_backup_20260720.

CREATE TABLE `ctrip_ota_promotion_status_new` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `hotel_id` VARCHAR(64) NOT NULL,
    `hotel_name` VARCHAR(255) NOT NULL,
    `platform_scope` VARCHAR(20) NOT NULL,
    `activity_code` VARCHAR(64) NOT NULL,
    `activity_name` VARCHAR(64) NOT NULL,
    `enabled` TINYINT NOT NULL,
    `status` VARCHAR(32) NOT NULL,
    `status_detail` VARCHAR(64) NULL,
    `room_type_count` INT UNSIGNED NULL,
    `orders_30d` INT UNSIGNED NULL,
    `snapshot_time` DATETIME NOT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ctrip_promotion_status` (`hotel_id`, `platform_scope`, `activity_code`),
    KEY `idx_ctrip_promotion_status_snapshot` (`hotel_id`, `snapshot_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Ctrip current promotion and service participation status by activity';

INSERT INTO `ctrip_ota_promotion_status_new` (
    hotel_id, hotel_name, platform_scope, activity_code, activity_name,
    enabled, status, status_detail, room_type_count, orders_30d, snapshot_time
)
SELECT hotel_id, hotel_name, platform_scope, 'points_alliance', '积分联盟',
       points_alliance_enabled,
       IF(points_alliance_enabled = 1, 'JOINED', 'NOT_JOINED'),
       NULL, NULL, points_alliance_orders_30d, snapshot_time
FROM `ctrip_ota_promotion_status` WHERE points_alliance_enabled IS NOT NULL
UNION ALL
SELECT hotel_id, hotel_name, platform_scope, 'preferred_club', '优享会',
       preferred_club_enabled,
       IF(preferred_club_enabled = 1, 'JOINED', 'NOT_JOINED'),
       preferred_club_tag_status, NULL, NULL, snapshot_time
FROM `ctrip_ota_promotion_status` WHERE preferred_club_enabled IS NOT NULL
UNION ALL
SELECT hotel_id, hotel_name, platform_scope, 'business_travel_price', '商旅专享价',
       business_travel_price_enabled,
       IF(business_travel_price_enabled = 1, 'JOINED', 'NOT_JOINED'),
       NULL, business_travel_room_count, business_travel_orders_30d, snapshot_time
FROM `ctrip_ota_promotion_status` WHERE business_travel_price_enabled IS NOT NULL
UNION ALL
SELECT hotel_id, hotel_name, platform_scope, 'flash_stay', '闪住',
       flash_stay_enabled,
       IF(flash_stay_enabled = 1, 'ENABLED', 'DISABLED'),
       NULL, NULL, NULL, snapshot_time
FROM `ctrip_ota_promotion_status` WHERE flash_stay_enabled IS NOT NULL
UNION ALL
SELECT hotel_id, hotel_name, platform_scope, 'hourly_room', '钟点房',
       hourly_room_enabled,
       IF(hourly_room_enabled = 1, 'ENABLED', 'DISABLED'),
       NULL, hourly_room_type_count, hourly_orders_30d, snapshot_time
FROM `ctrip_ota_promotion_status` WHERE hourly_room_enabled IS NOT NULL
UNION ALL
SELECT hotel_id, hotel_name, platform_scope, 'hourly_promotion', '钟点房促销',
       hourly_promotion_enabled,
       IF(hourly_promotion_enabled = 1, 'ENABLED', 'DISABLED'),
       NULL, NULL, hourly_orders_30d, snapshot_time
FROM `ctrip_ota_promotion_status` WHERE hourly_promotion_enabled IS NOT NULL
UNION ALL
SELECT hotel_id, hotel_name, platform_scope, 'travel_photo', '旅拍',
       travel_photo_uploaded,
       IF(travel_photo_uploaded = 1, 'UPLOADED', 'NOT_UPLOADED'),
       IF(travel_photo_claimed = 1, 'CLAIMED', IF(travel_photo_claimed = 0, 'NOT_CLAIMED', NULL)),
       NULL, NULL, snapshot_time
FROM `ctrip_ota_promotion_status` WHERE travel_photo_uploaded IS NOT NULL
UNION ALL
SELECT hotel_id, hotel_name, platform_scope, 'homepage_video', '首页视频',
       homepage_video_uploaded,
       IF(homepage_video_uploaded = 1, 'UPLOADED', 'NOT_UPLOADED'),
       NULL, NULL, NULL, snapshot_time
FROM `ctrip_ota_promotion_status` WHERE homepage_video_uploaded IS NOT NULL;

RENAME TABLE `ctrip_ota_promotion_status` TO `ctrip_ota_promotion_status_wide_backup_20260720`,
             `ctrip_ota_promotion_status_new` TO `ctrip_ota_promotion_status`;
