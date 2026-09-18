CREATE TABLE IF NOT EXISTS `ctrip_ota_video_upload_status` (
  `hotel_id` varchar(64) NOT NULL,
  `video_type` varchar(32) NOT NULL,
  `uploaded_count` int unsigned NOT NULL DEFAULT '0',
  `required_count` int unsigned NOT NULL DEFAULT '0',
  `status` varchar(16) NOT NULL,
  `snapshot_time` datetime NOT NULL COMMENT '采集时间',
  PRIMARY KEY (`hotel_id`,`video_type`),
  KEY `idx_ctrip_video_upload_snapshot` (`hotel_id`,`snapshot_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DELETE FROM `ctrip_ota_promotion_status` WHERE `activity_code` = 'homepage_video';
