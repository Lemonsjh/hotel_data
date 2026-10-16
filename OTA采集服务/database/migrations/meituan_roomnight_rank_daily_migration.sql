-- Table: meituan_ota_roomnight_rank_daily
CREATE TABLE IF NOT EXISTS `meituan_ota_roomnight_rank_daily` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) NOT NULL,
  `business_date` date NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `subject_poi_id` varchar(32) NOT NULL COMMENT '当前查询酒店 poiId',
  `rank_type` varchar(16) NOT NULL COMMENT 'P_RZ 入住间夜榜',
  `dim_name` varchar(64) NOT NULL,
  `rank` int unsigned NOT NULL,
  `poi_id` varchar(32) NOT NULL COMMENT '上榜酒店 poiId，按字符串保存',
  `poi_name` varchar(255) NOT NULL,
  `percent` decimal(9,4) NOT NULL COMMENT '接口原始 percent，非酒店入住率；不推算市场份额',
  `data_value` decimal(20,4) DEFAULT NULL COMMENT '接口 dataValue，null 不补零',
  `is_current_hotel` tinyint unsigned NOT NULL,
  `vip_tag` tinyint unsigned NOT NULL,
  `activity_list_json` json NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_roomnight_rank_day` (`hotel_id`,`business_date`,`subject_poi_id`,`rank_type`,`poi_id`),
  KEY `idx_roomnight_rank_snapshot` (`hotel_id`,`snapshot_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='美团每日入住间夜榜原始排名与百分比';
