-- Hotel data collection service schema
-- Execute after selecting the target database, for example:
--   mysql -u <user> -p <database_name> < create_all_tables.sql
-- This file contains schema only. It does not create a database or insert data.

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- Table: ctrip_ota_activity_product_detail
CREATE TABLE IF NOT EXISTS `ctrip_ota_activity_product_detail` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `platform_scope` varchar(20) NOT NULL DEFAULT 'ctrip',
  `hotel_id` varchar(100) DEFAULT NULL,
  `activity_source_type` varchar(100) DEFAULT NULL,
  `activity_name` varchar(255) DEFAULT NULL,
  `ota_room_type_id` varchar(100) DEFAULT NULL,
  `room_type_name` varchar(255) DEFAULT NULL,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `remaining_inventory` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ctrip_product_snapshot` (`snapshot_time`,`channel_source`),
  KEY `idx_ctrip_product_activity` (`activity_name`),
  KEY `idx_ctrip_product_room` (`ota_room_type_id`,`room_type_name`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: ctrip_ota_business_metrics
CREATE TABLE IF NOT EXISTS `ctrip_ota_business_metrics` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `business_date` datetime NOT NULL,
  `metric_code` varchar(64) NOT NULL,
  `hotel_name` varchar(255) DEFAULT NULL,
  `hotel_id` varchar(100) NOT NULL,
  `platform_scope` varchar(20) NOT NULL DEFAULT 'ctrip',
  `metric_group` varchar(100) DEFAULT NULL,
  `metric_name` varchar(100) DEFAULT NULL,
  `metric_value` decimal(18,4) DEFAULT NULL,
  `metric_unit` varchar(50) DEFAULT NULL,
  `compare_label` varchar(100) DEFAULT NULL,
  `compare_value` varchar(100) DEFAULT NULL,
  `competitor_rank` varchar(100) DEFAULT NULL,
  `peer_average` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ctrip_business_metric_daily` (`hotel_id`,`platform_scope`,`business_date`,`metric_code`),
  KEY `idx_ctrip_business_snapshot` (`snapshot_time`),
  KEY `idx_ctrip_business_metric` (`hotel_name`,`metric_name`),
  KEY `idx_ctrip_business_date` (`hotel_id`,`business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: ctrip_ota_competition_metrics_30d
CREATE TABLE IF NOT EXISTS `ctrip_ota_competition_metrics_30d` (
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_scope` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'ctrip',
  `metric_code` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `metric_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `metric_unit` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `period_start_date` date NOT NULL,
  `period_end_date` date NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `hotel_value` decimal(18,4) DEFAULT NULL,
  `previous_value` decimal(18,4) DEFAULT NULL,
  `competitor_avg` decimal(18,4) DEFAULT NULL,
  `competitor_rank` int DEFAULT NULL,
  `previous_rank` int DEFAULT NULL,
  `competition_circle_hotel_count` int DEFAULT NULL,
  PRIMARY KEY (`hotel_id`,`platform_scope`,`metric_code`),
  KEY `idx_ctrip_competition_period_end` (`hotel_id`,`period_end_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: ctrip_ota_flow_conversion_30d
CREATE TABLE IF NOT EXISTS `ctrip_ota_flow_conversion_30d` (
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '内部酒店 ID',
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_scope` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'ctrip 或 qunar',
  `ota_hotel_id` bigint DEFAULT NULL COMMENT '携程后台酒店 ID；竞争圈平均为空',
  `business_date` date NOT NULL,
  `period_start_date` date NOT NULL,
  `period_end_date` date NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `app_visitors` bigint DEFAULT NULL,
  `peer_app_visitors` bigint DEFAULT NULL,
  `list_exposure` bigint DEFAULT NULL COMMENT '列表页曝光量',
  `detail_exposure` bigint DEFAULT NULL COMMENT '详情页访客量',
  `exposure_to_detail_rate_pct` decimal(9,4) DEFAULT NULL COMMENT '曝光转化率',
  `order_filling_count` bigint DEFAULT NULL COMMENT '订单页访客量',
  `order_submit_count` bigint DEFAULT NULL COMMENT '订单提交人数',
  `detail_to_order_rate_pct` decimal(9,4) DEFAULT NULL,
  `order_to_submit_rate_pct` decimal(9,4) DEFAULT NULL,
  `peer_list_exposure` bigint DEFAULT NULL,
  `peer_detail_exposure` bigint DEFAULT NULL,
  `peer_exposure_to_detail_rate_pct` decimal(9,4) DEFAULT NULL,
  `peer_order_filling_count` bigint DEFAULT NULL,
  `peer_order_submit_count` bigint DEFAULT NULL,
  `peer_detail_to_order_rate_pct` decimal(9,4) DEFAULT NULL,
  `peer_order_to_submit_rate_pct` decimal(9,4) DEFAULT NULL,
  `list_exposure_peer_rank` int DEFAULT NULL,
  `detail_exposure_peer_rank` int DEFAULT NULL,
  `order_filling_peer_rank` int DEFAULT NULL,
  `exposure_to_detail_rate_peer_rank` int DEFAULT NULL,
  `detail_to_order_rate_peer_rank` int DEFAULT NULL,
  PRIMARY KEY (`hotel_id`,`platform_scope`),
  KEY `idx_ctrip_flow_period_end` (`hotel_id`,`period_end_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: ctrip_ota_goods_price_mapping
CREATE TABLE IF NOT EXISTS `ctrip_ota_goods_price_mapping` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `platform_scope` varchar(20) NOT NULL DEFAULT 'ctrip',
  `hotel_id` varchar(100) DEFAULT NULL,
  `ota_hotel_id` varchar(100) DEFAULT NULL,
  `ota_room_type_id` varchar(100) DEFAULT NULL,
  `room_type_name` varchar(255) DEFAULT NULL,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `business_date` date DEFAULT NULL,
  `ota_product_id` varchar(100) DEFAULT NULL,
  `ota_product_name` varchar(500) DEFAULT NULL,
  `product_cipher` text,
  `price_editable_flag` tinyint(1) DEFAULT NULL,
  `is_hour_room` tinyint(1) DEFAULT NULL,
  `ota_sale_price` decimal(10,2) DEFAULT NULL,
  `commission_rate` varchar(20) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ctrip_goods_price_snapshot` (`snapshot_time`,`channel_source`),
  KEY `idx_ctrip_goods_price_business_date` (`business_date`),
  KEY `idx_ctrip_goods_price_product` (`ota_product_id`),
  KEY `idx_ctrip_goods_price_room` (`ota_room_type_id`,`room_type_name`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: ctrip_ota_joined_rights
CREATE TABLE IF NOT EXISTS `ctrip_ota_joined_rights` (
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_scope` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `right_type_id` bigint DEFAULT NULL,
  `right_type` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `right_name` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  `applicable_room_types` text COLLATE utf8mb4_unicode_ci,
  `invalid_dates` text COLLATE utf8mb4_unicode_ci,
  `rights_rules` text COLLATE utf8mb4_unicode_ci,
  `stock_use_conditions` text COLLATE utf8mb4_unicode_ci,
  `right_status` varchar(40) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `snapshot_time` datetime NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`hotel_id`,`platform_scope`,`right_type`),
  KEY `idx_ctrip_joined_right_snapshot` (`hotel_id`,`platform_scope`,`snapshot_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: ctrip_ota_order_detail
CREATE TABLE IF NOT EXISTS `ctrip_ota_order_detail` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_scope` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `ctrip_hotel_id` bigint DEFAULT NULL,
  `form_id` bigint unsigned NOT NULL,
  `order_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `order_source_type` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `ctrip_source_type` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `alliance_name` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `order_type` varchar(16) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `order_type_name` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `order_status_code` int DEFAULT NULL,
  `order_status` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `order_status_type` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `booking_time` datetime DEFAULT NULL,
  `arrival_date` date NOT NULL,
  `departure_date` date DEFAULT NULL,
  `room_type_name` text COLLATE utf8mb4_unicode_ci,
  `room_quantity` int DEFAULT NULL,
  `room_night_count` int DEFAULT NULL,
  `guest_count` int DEFAULT NULL,
  `currency` varchar(12) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `payment_type` varchar(16) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `payment_term` varchar(16) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `is_auto_confirmed` tinyint DEFAULT NULL,
  `is_guaranteed` tinyint DEFAULT NULL,
  `is_hour_room` tinyint DEFAULT NULL,
  `is_credit_order` tinyint DEFAULT NULL,
  `is_free_room_order` tinyint DEFAULT NULL,
  `snapshot_time` datetime NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ctrip_order_detail` (`hotel_id`,`form_id`),
  KEY `idx_ctrip_order_arrival` (`hotel_id`,`arrival_date`),
  KEY `idx_ctrip_order_platform_arrival` (`platform_scope`,`arrival_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: ctrip_ota_order_loss_monthly
CREATE TABLE IF NOT EXISTS `ctrip_ota_order_loss_monthly` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_scope` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `business_date` date NOT NULL,
  `period_start_date` date NOT NULL,
  `period_end_date` date NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `ranking_position` int NOT NULL,
  `competitor_hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `common_browse_rate_pct` decimal(9,4) DEFAULT NULL,
  `order_conversion_rate_pct` decimal(9,4) DEFAULT NULL,
  `loss_order_count` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ctrip_order_loss_monthly` (`hotel_id`,`platform_scope`,`ranking_position`),
  KEY `idx_ctrip_order_loss_period` (`hotel_id`,`period_end_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: ctrip_ota_promotion_activity
CREATE TABLE IF NOT EXISTS `ctrip_ota_promotion_activity` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `platform_scope` varchar(20) NOT NULL DEFAULT 'ctrip',
  `hotel_id` varchar(100) DEFAULT NULL,
  `activity_source_type` varchar(100) DEFAULT NULL,
  `activity_name` varchar(255) DEFAULT NULL,
  `activity_status` varchar(100) DEFAULT NULL,
  `activity_time_range` varchar(255) DEFAULT NULL,
  `activity_rule_labels` text,
  `activity_room_type_summary` text,
  PRIMARY KEY (`id`),
  KEY `idx_ctrip_promotion_snapshot` (`snapshot_time`,`channel_source`),
  KEY `idx_ctrip_promotion_activity` (`activity_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: ctrip_ota_promotion_performance_30d
CREATE TABLE IF NOT EXISTS `ctrip_ota_promotion_performance_30d` (
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_scope` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `period_start_date` date NOT NULL,
  `period_end_date` date NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `exposure_count` bigint DEFAULT NULL,
  `click_count` bigint DEFAULT NULL,
  `click_rate_pct` decimal(10,2) DEFAULT NULL,
  `spend_amount` decimal(14,2) DEFAULT NULL,
  `bonus_spend_amount` decimal(14,2) DEFAULT NULL,
  `cash_spend_amount` decimal(14,2) DEFAULT NULL,
  `cost_per_click` decimal(14,4) DEFAULT NULL,
  `booking_order_count` bigint DEFAULT NULL,
  `booking_order_amount` decimal(14,2) DEFAULT NULL,
  `room_night_count` bigint DEFAULT NULL,
  `conversion_rate_pct` decimal(10,2) DEFAULT NULL,
  `return_on_ad_spend` decimal(14,4) DEFAULT NULL,
  `avg_exposure_position` decimal(10,2) DEFAULT NULL,
  `avg_click_position` decimal(10,2) DEFAULT NULL,
  `ebk_order_count` bigint DEFAULT NULL,
  `other_order_count` bigint DEFAULT NULL,
  `data_delayed` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`hotel_id`,`platform_scope`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: ctrip_ota_promotion_status
CREATE TABLE IF NOT EXISTS `ctrip_ota_promotion_status` (
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_scope` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `activity_code` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `activity_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `enabled` tinyint DEFAULT NULL,
  `metric_value` decimal(10,2) DEFAULT NULL,
  `metric_unit` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `status` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status_detail` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `room_type_count` int unsigned DEFAULT NULL,
  `orders_30d` int unsigned DEFAULT NULL,
  `snapshot_time` datetime NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`hotel_id`,`platform_scope`,`activity_code`),
  KEY `idx_ctrip_promotion_status_snapshot` (`hotel_id`,`snapshot_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: ctrip_ota_psi_metric
CREATE TABLE IF NOT EXISTS `ctrip_ota_psi_metric` (
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_scope` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'ctrip',
  `business_date` date NOT NULL,
  `metric_code` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `metric_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `metric_value` decimal(18,4) DEFAULT NULL,
  `metric_unit` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `psi_score` decimal(8,2) DEFAULT NULL,
  `weight_pct` decimal(6,2) DEFAULT NULL,
  `competition_rank` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `score_gap` decimal(18,4) DEFAULT NULL,
  `score_gap_unit` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `period_start_date` date DEFAULT NULL,
  `period_end_date` date DEFAULT NULL,
  `snapshot_time` datetime NOT NULL,
  PRIMARY KEY (`hotel_id`,`platform_scope`,`metric_code`),
  KEY `idx_ctrip_psi_metric_date` (`hotel_id`,`business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: ctrip_ota_psi_score
CREATE TABLE IF NOT EXISTS `ctrip_ota_psi_score` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_scope` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `business_date` date NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `psi_total_score` decimal(8,2) DEFAULT NULL,
  `psi_basic_score` decimal(8,2) DEFAULT NULL,
  `psi_basic_score_max` decimal(8,2) DEFAULT NULL,
  `psi_reward_score` decimal(8,2) DEFAULT NULL,
  `psi_reward_score_max` decimal(8,2) DEFAULT NULL,
  `psi_deduction_score` decimal(8,2) DEFAULT NULL,
  `score_psi` decimal(8,2) DEFAULT NULL,
  `service_deduction_score` decimal(8,2) DEFAULT NULL,
  `integrity_deduction_score` decimal(8,2) DEFAULT NULL,
  `financial_deduction_score` decimal(8,2) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ctrip_psi_score_daily` (`hotel_id`,`platform_scope`,`business_date`),
  KEY `idx_ctrip_psi_score_date` (`business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: ctrip_ota_review_detail
CREATE TABLE IF NOT EXISTS `ctrip_ota_review_detail` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `platform_scope` varchar(20) NOT NULL DEFAULT 'ctrip',
  `hotel_id` varchar(100) DEFAULT NULL,
  `hotel_name` varchar(255) DEFAULT NULL,
  `poi_id` varchar(100) NOT NULL,
  `review_id` varchar(100) NOT NULL,
  `reviewer_name_masked` varchar(100) DEFAULT NULL,
  `review_score` decimal(10,2) DEFAULT NULL,
  `review_content` text,
  `review_time` datetime DEFAULT NULL,
  `stay_date` date DEFAULT NULL,
  `merchant_reply_content` text,
  `merchant_reply_time` datetime DEFAULT NULL,
  `is_replied` tinyint(1) DEFAULT NULL,
  `room_type_name` text,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `ota_product_name` text,
  `has_image` tinyint(1) DEFAULT NULL,
  `image_count` int DEFAULT NULL,
  `image_urls_json` longtext,
  `is_anonymous` tinyint(1) DEFAULT NULL,
  `is_negative_review` tinyint(1) DEFAULT NULL,
  `read_status` int DEFAULT NULL,
  `hygiene_score` decimal(10,2) DEFAULT NULL,
  `facility_score` decimal(10,2) DEFAULT NULL,
  `location_score` decimal(10,2) DEFAULT NULL,
  `service_score` decimal(10,2) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ctrip_review_detail` (`channel_source`,`platform_scope`,`poi_id`,`review_id`),
  KEY `idx_ctrip_review_detail_time` (`review_time`),
  KEY `idx_ctrip_review_detail_status` (`is_negative_review`,`is_replied`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: ctrip_ota_review_overview
CREATE TABLE IF NOT EXISTS `ctrip_ota_review_overview` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `platform_scope` varchar(20) NOT NULL DEFAULT 'ctrip',
  `hotel_id` varchar(100) DEFAULT NULL,
  `review_score` decimal(10,2) DEFAULT NULL,
  `review_score_max` decimal(10,2) DEFAULT NULL,
  `environment_score` decimal(10,2) DEFAULT NULL,
  `facility_score` decimal(10,2) DEFAULT NULL,
  `style_score` decimal(10,2) DEFAULT NULL,
  `safety_score` decimal(10,2) DEFAULT NULL,
  `service_score` decimal(10,2) DEFAULT NULL,
  `hygiene_score` decimal(10,2) DEFAULT NULL,
  `total_review_count` int DEFAULT NULL,
  `unreplied_review_count` int DEFAULT NULL,
  `negative_review_count` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ctrip_review_overview_snapshot` (`snapshot_time`,`channel_source`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: ctrip_ota_review_ranking
CREATE TABLE IF NOT EXISTS `ctrip_ota_review_ranking` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `platform_scope` varchar(20) NOT NULL DEFAULT 'ctrip',
  `hotel_id` varchar(100) DEFAULT NULL,
  `ranking_type` varchar(100) DEFAULT NULL,
  `ranking_position` int DEFAULT NULL,
  `rank_item_name` varchar(255) DEFAULT NULL,
  `rank_item_value` decimal(18,4) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ctrip_review_ranking_snapshot` (`snapshot_time`,`channel_source`,`ranking_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: ctrip_ota_userprofile_distribution
CREATE TABLE IF NOT EXISTS `ctrip_ota_userprofile_distribution` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_scope` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `dimension_code` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `bucket_label` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `rate_pct` decimal(9,4) DEFAULT NULL,
  `metric_value` decimal(12,4) DEFAULT NULL,
  `metric_unit` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `rank_position` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ctrip_userprofile_distribution` (`hotel_id`,`platform_scope`,`dimension_code`,`bucket_label`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: ctrip_price_task
CREATE TABLE IF NOT EXISTS `ctrip_price_task` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '数据库内部主键',
  `task_id` varchar(64) DEFAULT NULL COMMENT '业务任务号',
  `hotel_id` varchar(100) NOT NULL DEFAULT '' COMMENT '酒店主 ID',
  `hotel_name` varchar(100) NOT NULL COMMENT '酒店展示名',
  `channel_source` varchar(32) NOT NULL DEFAULT 'ctrip' COMMENT '渠道来源',
  `room_type_id` varchar(100) DEFAULT NULL COMMENT 'PMS/内部房型 ID',
  `room_type_name` varchar(200) NOT NULL COMMENT '房型名称',
  `ota_room_type_id` varchar(100) DEFAULT NULL COMMENT '携程侧房型 ID',
  `ota_product_id` bigint NOT NULL COMMENT '携程商品 ID',
  `ota_product_name` varchar(200) DEFAULT NULL COMMENT '携程商品名称',
  `rate_plan_name` varchar(200) DEFAULT NULL COMMENT '携程价格计划名称',
  `business_date` date NOT NULL COMMENT '售卖/入住日期',
  `current_sale_price` decimal(10,2) DEFAULT NULL COMMENT '当前售价',
  `target_sale_price` decimal(10,2) NOT NULL COMMENT '目标售价',
  `price_delta` decimal(10,2) DEFAULT NULL COMMENT '目标价减当前价',
  `price_delta_pct` decimal(10,4) DEFAULT NULL COMMENT '调价百分比',
  `product_cipher` text NOT NULL COMMENT '携程加密商品标识',
  `price_editable_flag` tinyint(1) DEFAULT NULL COMMENT '是否可编辑价格',
  `is_hour_room` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否钟点房',
  `execute_status` enum('PENDING','EXECUTING','SUCCESS','FAILED') NOT NULL DEFAULT 'PENDING' COMMENT '兼容旧插件的总执行状态',
  `review_status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT '人工审查状态',
  `plugin_status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT '插件处理状态',
  `verification_status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT '平台回查状态',
  `source_decision_id` varchar(128) DEFAULT NULL COMMENT '来源收益决策 ID',
  `approval_id` varchar(128) DEFAULT NULL COMMENT '审批 ID',
  `created_by` varchar(128) DEFAULT NULL COMMENT '创建者',
  `approved_by` varchar(128) DEFAULT NULL COMMENT '审批人',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `approved_at` datetime DEFAULT NULL COMMENT '审批时间',
  `queued_at` datetime DEFAULT NULL COMMENT '入队时间',
  `plugin_picked_at` datetime DEFAULT NULL COMMENT '插件领取时间',
  `executed_at` datetime DEFAULT NULL COMMENT '插件执行完成时间',
  `verified_at` datetime DEFAULT NULL COMMENT '平台回查完成时间',
  `platform_actual_price` decimal(10,2) DEFAULT NULL COMMENT '平台实际价格',
  `verification_message` varchar(512) DEFAULT NULL COMMENT '回查说明',
  `error_code` varchar(64) DEFAULT NULL COMMENT '错误码',
  `error_message` varchar(1000) DEFAULT NULL COMMENT '错误说明',
  `retry_count` int unsigned NOT NULL DEFAULT '0' COMMENT '重试次数',
  `last_retry_at` datetime DEFAULT NULL COMMENT '最近重试时间',
  `payload_json` json DEFAULT NULL COMMENT '创建任务上下文',
  `result_json` json DEFAULT NULL COMMENT '插件/回查结果',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_task` (`hotel_id`,`hotel_name`,`channel_source`,`ota_product_id`,`business_date`),
  UNIQUE KEY `uk_task_id` (`task_id`),
  KEY `idx_status` (`execute_status`),
  KEY `idx_workflow_status` (`review_status`,`plugin_status`,`verification_status`),
  KEY `idx_hotel_id` (`hotel_id`),
  KEY `idx_hotel_date` (`hotel_name`,`business_date`),
  KEY `idx_product_date` (`ota_product_id`,`business_date`),
  KEY `idx_source_decision` (`source_decision_id`),
  KEY `idx_approval` (`approval_id`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='携程调价任务';

-- Table: hotel_room_type_mapping
CREATE TABLE IF NOT EXISTS `hotel_room_type_mapping` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '表内主键，自增ID',
  `hotel_id` varchar(50) NOT NULL COMMENT '系统统一酒店编号，例如HT01、xingfeng',
  `pms_hotel_name` varchar(255) NOT NULL DEFAULT '' COMMENT 'PMS侧酒店名称',
  `room_type_id` varchar(50) NOT NULL COMMENT '系统统一房型编号，例如TY01、KING',
  `room_type_name` varchar(255) NOT NULL COMMENT '系统统一房型名称',
  `pms_room_type_name` varchar(255) NOT NULL DEFAULT '' COMMENT 'PMS侧房型名称',
  `source_platform` varchar(64) NOT NULL COMMENT '来源平台，与业务表保持一致：PMS（别样红）、美团、携程、飞猪',
  `ota_hotel_name` varchar(255) NOT NULL DEFAULT '' COMMENT 'OTA渠道酒店名称',
  `source_room_type_name` varchar(255) NOT NULL COMMENT '来源平台原始房型名称',
  `ota_room_type_name` varchar(255) NOT NULL DEFAULT '' COMMENT 'OTA渠道房型名称',
  `source_product_id` varchar(100) NOT NULL DEFAULT '' COMMENT '来源平台商品ID/价格计划ID，例如美团goods_id、携程商品ID',
  `source_product_name` varchar(500) NOT NULL DEFAULT '' COMMENT '来源平台商品名称',
  `rate_plan_name` varchar(500) NOT NULL DEFAULT '' COMMENT '价格计划名称，例如标准价、无早、不可取消',
  `product_cipher` varchar(255) NOT NULL DEFAULT '' COMMENT '携程商品加密标识，敏感字段，飞书端不要明文输出',
  `price_editable_flag` tinyint(1) DEFAULT NULL COMMENT '是否允许改价：1可改价，0不可改价，空未知',
  `is_hour_room` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否钟点房：1钟点房，0非钟点房',
  `mapping_status` enum('CONFIRMED','AUTO','PENDING','CONFLICT','REJECTED') NOT NULL DEFAULT 'PENDING' COMMENT '映射状态',
  `match_rule` enum('MANUAL','ROOM_NAME','GOODS_ID','ROOM_ID','PRODUCT_ID','ROOM_NAME_FUZZY','NONE') NOT NULL DEFAULT 'MANUAL' COMMENT '映射规则',
  `match_confidence` decimal(4,2) NOT NULL DEFAULT '1.00' COMMENT '自动匹配置信度，0-1，人工确认填1.00',
  `reviewed_by` varchar(100) DEFAULT NULL COMMENT '人工确认人',
  `reviewed_at` datetime DEFAULT NULL COMMENT '人工确认时间',
  `review_note` varchar(500) DEFAULT NULL COMMENT '人工确认备注',
  `is_active` tinyint(1) NOT NULL DEFAULT '1' COMMENT '是否启用：1启用，0停用',
  `first_seen_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '第一次发现时间',
  `last_seen_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近一次发现时间',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '映射记录创建时间',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '映射记录更新时间',
  `remark` varchar(500) DEFAULT NULL COMMENT '备注',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_source_product_mapping` (`hotel_id`,`room_type_id`,`source_platform`,`source_room_type_name`,`source_product_id`),
  KEY `idx_hotel_id` (`hotel_id`),
  KEY `idx_room_type_id` (`room_type_id`),
  KEY `idx_source_platform` (`source_platform`),
  KEY `idx_source_product` (`source_platform`,`source_product_id`),
  KEY `idx_mapping_status` (`mapping_status`,`is_active`),
  KEY `idx_match_rule` (`match_rule`,`match_confidence`),
  KEY `idx_source_room` (`source_platform`,`source_room_type_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='酒店房型与平台商品统一映射表';

-- Table: jd01_booking_detail
CREATE TABLE IF NOT EXISTS `jd01_booking_detail` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `hotel_name` varchar(255) DEFAULT NULL,
  `hotel_id` varchar(100) NOT NULL DEFAULT '',
  `source_platform` varchar(64) DEFAULT 'PMS（别样红）',
  `order_id` varchar(100) DEFAULT NULL,
  `booking_time` datetime DEFAULT NULL,
  `contact` varchar(100) DEFAULT NULL,
  `guest_source` varchar(100) DEFAULT NULL,
  `member_level` varchar(50) DEFAULT NULL,
  `arrival_time` datetime DEFAULT NULL,
  `departure_time` datetime DEFAULT NULL,
  `room_type_name` varchar(255) DEFAULT NULL,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `room_count` int DEFAULT NULL,
  `price_type` varchar(100) DEFAULT NULL,
  `room_price` decimal(10,2) DEFAULT NULL,
  `prepayment` decimal(10,2) DEFAULT NULL,
  `guarantee_method` varchar(100) DEFAULT NULL,
  `booking_status` varchar(100) DEFAULT NULL,
  `hold_time` datetime DEFAULT NULL,
  `remarks` text,
  `operator_name` varchar(100) DEFAULT NULL,
  `snapshot_time` datetime DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_hotel_id` (`hotel_id`),
  KEY `idx_order_id` (`order_id`),
  KEY `idx_booking_time` (`booking_time`),
  KEY `idx_arrival_time` (`arrival_time`),
  KEY `idx_snapshot_time` (`snapshot_time`),
  KEY `idx_hotel_platform` (`hotel_name`,`source_platform`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: jd04_inhouse_extension
CREATE TABLE IF NOT EXISTS `jd04_inhouse_extension` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `hotel_name` varchar(255) DEFAULT NULL,
  `hotel_id` varchar(100) NOT NULL DEFAULT '',
  `source_platform` varchar(64) DEFAULT 'PMS（别样红）',
  `order_id` varchar(100) DEFAULT NULL,
  `channel_source` varchar(100) DEFAULT NULL,
  `guest_source` varchar(100) DEFAULT NULL,
  `checkin_type` varchar(100) DEFAULT NULL,
  `guest_name` varchar(100) DEFAULT NULL,
  `room_type_name` varchar(255) DEFAULT NULL,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `room_no` varchar(50) DEFAULT NULL,
  `price_type` varchar(100) DEFAULT NULL,
  `room_price` decimal(10,2) DEFAULT NULL,
  `checkin_time` datetime DEFAULT NULL,
  `original_checkout_time` datetime DEFAULT NULL,
  `checkout_time` datetime DEFAULT NULL,
  `op_type` varchar(100) DEFAULT NULL,
  `operator_name` varchar(100) DEFAULT NULL,
  `op_time` datetime DEFAULT NULL,
  `status` varchar(100) DEFAULT NULL,
  `snapshot_time` datetime DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_hotel_id` (`hotel_id`),
  KEY `idx_order_id` (`order_id`),
  KEY `idx_guest_name` (`guest_name`),
  KEY `idx_checkin_time` (`checkin_time`),
  KEY `idx_checkout_time` (`checkout_time`),
  KEY `idx_snapshot_time` (`snapshot_time`),
  KEY `idx_hotel_platform` (`hotel_name`,`source_platform`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: jl01_room_type_performance_daily
CREATE TABLE IF NOT EXISTS `jl01_room_type_performance_daily` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '内部主键',
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '酒店内部 ID',
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '酒店名称',
  `source_platform` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PMS（别样红）' COMMENT '数据来源',
  `business_date` date NOT NULL COMMENT '营业日',
  `room_type_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'PMS 房型名称',
  `room_type_id` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '系统统一房型 ID',
  `pms_rate_room_type_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT 'JL01 费率房型标识',
  `room_nights` decimal(18,4) DEFAULT NULL COMMENT '间夜数',
  `occupancy_rate` decimal(9,4) DEFAULT NULL COMMENT '出租率，百分比数值',
  `room_revenue` decimal(18,4) DEFAULT NULL COMMENT '房费',
  `adr` decimal(18,4) DEFAULT NULL COMMENT '平均房价',
  `revpar` decimal(18,4) DEFAULT NULL COMMENT 'RevPar',
  `snapshot_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '采集时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_jl01_hotel_date_room` (`hotel_id`,`business_date`,`room_type_name`),
  KEY `idx_jl01_room_type_id` (`hotel_id`,`room_type_id`),
  KEY `idx_jl01_business_date` (`hotel_id`,`business_date`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='PMS JL01 按房型实际经营日报';

-- Table: jl02_hotel_performance_daily
CREATE TABLE IF NOT EXISTS `jl02_hotel_performance_daily` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '内部主键',
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '酒店内部 ID',
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '酒店名称',
  `source_platform` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PMS（别样红）' COMMENT '数据来源',
  `business_date` date NOT NULL COMMENT 'PMS 营业日',
  `category` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '报表类别',
  `metric_name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '统计项目',
  `value_day` decimal(18,4) DEFAULT NULL COMMENT '本日值，百分比按数值保存',
  `value_month` decimal(18,4) DEFAULT NULL COMMENT '本月累计值，百分比按数值保存',
  `value_year` decimal(18,4) DEFAULT NULL COMMENT '本年累计值，百分比按数值保存',
  `snapshot_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '采集时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_jl02_hotel_date_metric` (`hotel_id`,`business_date`,`category`,`metric_name`),
  KEY `idx_jl02_business_date` (`hotel_id`,`business_date`),
  KEY `idx_hotel_room_type_id` (`hotel_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='PMS JL02 酒店经营业绩日报';

-- Table: jl11_room_type_classification
CREATE TABLE IF NOT EXISTS `jl11_room_type_classification` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `source_platform` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PMS',
  `snapshot_date` date NOT NULL,
  `period_start` date NOT NULL,
  `period_end` date NOT NULL,
  `section` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `room_type_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `room_type_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `dimension_code` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '',
  `dimension_name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '',
  `room_count` decimal(18,4) DEFAULT NULL,
  `room_nights` decimal(18,4) DEFAULT NULL,
  `occupancy_rate` decimal(9,4) DEFAULT NULL,
  `room_revenue` decimal(18,4) DEFAULT NULL,
  `average_room_price` decimal(18,4) DEFAULT NULL,
  `revpar` decimal(18,4) DEFAULT NULL,
  `overnight_room_count` decimal(18,4) DEFAULT NULL,
  `overnight_occupancy_rate` decimal(9,4) DEFAULT NULL,
  `snapshot_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_jl11_snapshot_row` (`hotel_id`,`snapshot_date`,`section`,`room_type_name`,`dimension_code`),
  KEY `idx_jl11_period` (`hotel_id`,`period_end`),
  KEY `idx_jl11_room_type` (`hotel_id`,`room_type_id`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: jy01_hotel_statistics_daily
CREATE TABLE IF NOT EXISTS `jy01_hotel_statistics_daily` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `hotel_name` varchar(255) NOT NULL,
  `hotel_id` varchar(100) NOT NULL DEFAULT '',
  `source_platform` varchar(64) NOT NULL DEFAULT 'PMS（别样红）',
  `business_date` date NOT NULL,
  `dimension_type` varchar(100) NOT NULL,
  `dimension_name` varchar(255) NOT NULL,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `room_count` int DEFAULT NULL,
  `room_nights` decimal(10,2) DEFAULT NULL,
  `room_revenue` decimal(18,2) DEFAULT NULL,
  `occupancy_rate` decimal(10,2) DEFAULT NULL,
  `adr` decimal(12,2) DEFAULT NULL,
  `revpar` decimal(12,2) DEFAULT NULL,
  `sold_rooms` int DEFAULT NULL,
  `remaining_rooms` int DEFAULT NULL,
  `orders_today` int DEFAULT NULL,
  `snapshot_time` datetime NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_daily_snapshot` (`hotel_name`(100),`source_platform`(32),`business_date`,`dimension_type`(50),`dimension_name`(100)),
  KEY `idx_hotel_id` (`hotel_id`),
  KEY `idx_business_date` (`business_date`),
  KEY `idx_dimension` (`dimension_type`,`dimension_name`),
  KEY `idx_snapshot_time` (`snapshot_time`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: jy03_hotel_statistics_month
CREATE TABLE IF NOT EXISTS `jy03_hotel_statistics_month` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `hotel_name` varchar(150) NOT NULL,
  `hotel_id` varchar(100) NOT NULL DEFAULT '',
  `source_platform` varchar(64) NOT NULL DEFAULT 'PMS（别样红）',
  `period_month` varchar(7) NOT NULL,
  `dimension_type` varchar(100) NOT NULL,
  `dimension_name` varchar(255) NOT NULL,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `room_count` int DEFAULT NULL,
  `room_nights` decimal(10,2) DEFAULT NULL,
  `room_revenue` decimal(18,2) DEFAULT NULL,
  `maintain_rooms` int DEFAULT NULL,
  `occupancy_rate` decimal(10,2) DEFAULT NULL,
  `adr` decimal(12,2) DEFAULT NULL,
  `revpar` decimal(12,2) DEFAULT NULL,
  `snapshot_time` datetime NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_month_snapshot` (`hotel_name`(100),`source_platform`(32),`period_month`,`dimension_type`(50),`dimension_name`(100)),
  KEY `idx_hotel_id` (`hotel_id`),
  KEY `idx_period_month` (`period_month`),
  KEY `idx_dimension` (`dimension_type`,`dimension_name`),
  KEY `idx_snapshot_time` (`snapshot_time`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: kf11_room_status_snapshot
CREATE TABLE IF NOT EXISTS `kf11_room_status_snapshot` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `hotel_name` varchar(150) NOT NULL,
  `hotel_id` varchar(100) NOT NULL DEFAULT '',
  `source_platform` varchar(64) NOT NULL DEFAULT 'PMS（别样红）',
  `business_date` date NOT NULL,
  `room_no` varchar(50) NOT NULL,
  `room_type_name` varchar(120) NOT NULL,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `room_status` varchar(50) NOT NULL,
  `guest_name` varchar(100) DEFAULT NULL,
  `checkin_time` datetime DEFAULT NULL,
  `checkout_time` datetime DEFAULT NULL,
  `snapshot_time` datetime NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_kf11_daily_room` (`hotel_id`,`source_platform`,`business_date`,`room_no`),
  KEY `idx_hotel_id` (`hotel_id`),
  KEY `idx_business_date` (`business_date`),
  KEY `idx_room_status` (`room_status`),
  KEY `idx_snapshot_time` (`snapshot_time`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_ota_activity_product_detail
CREATE TABLE IF NOT EXISTS `meituan_ota_activity_product_detail` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `hotel_id` varchar(100) DEFAULT NULL,
  `activity_source_type` varchar(100) DEFAULT NULL,
  `activity_id` varchar(100) DEFAULT NULL,
  `activity_name` varchar(255) DEFAULT NULL,
  `ota_product_id` varchar(100) DEFAULT NULL,
  `room_type_name` varchar(255) DEFAULT NULL,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `remaining_inventory` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_meituan_ota_product_snapshot` (`snapshot_time`,`channel_source`),
  KEY `idx_meituan_ota_product_activity` (`activity_id`,`activity_name`),
  KEY `idx_meituan_ota_product_room` (`ota_product_id`,`room_type_name`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_ota_business_metrics
CREATE TABLE IF NOT EXISTS `meituan_ota_business_metrics` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `business_date` datetime DEFAULT NULL,
  `metric_code` varchar(64) DEFAULT NULL COMMENT '??????',
  `hotel_name` varchar(255) DEFAULT NULL,
  `hotel_id` varchar(100) DEFAULT NULL,
  `metric_name` varchar(100) DEFAULT NULL,
  `metric_value` decimal(18,4) DEFAULT NULL,
  `metric_unit` varchar(50) DEFAULT NULL,
  `compare_label` varchar(100) DEFAULT NULL,
  `compare_value` varchar(100) DEFAULT NULL,
  `competitor_rank` varchar(100) DEFAULT NULL,
  `peer_average` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meituan_business_metric_daily` (`hotel_id`,`business_date`,`metric_code`),
  KEY `idx_meituan_ota_business_snapshot` (`snapshot_time`),
  KEY `idx_meituan_ota_business_metric` (`hotel_name`,`metric_name`),
  KEY `idx_meituan_business_date` (`hotel_id`,`business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_ota_exposure_source_daily
CREATE TABLE IF NOT EXISTS `meituan_ota_exposure_source_daily` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `business_date` date NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `total_exposure` bigint unsigned NOT NULL DEFAULT '0',
  `non_ad_exposure` bigint unsigned NOT NULL DEFAULT '0',
  `ad_exposure` bigint unsigned NOT NULL DEFAULT '0',
  `ad_exposure_ratio_pct` decimal(7,4) NOT NULL DEFAULT '0.0000',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meituan_exposure_source_daily` (`hotel_id`,`business_date`),
  KEY `idx_meituan_exposure_source_daily_snapshot` (`hotel_id`,`snapshot_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Meituan daily exposure-source detail';

-- Table: meituan_ota_exposure_source_monthly
CREATE TABLE IF NOT EXISTS `meituan_ota_exposure_source_monthly` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `business_date` date NOT NULL COMMENT '近30天统计窗口结束日',
  `snapshot_time` datetime NOT NULL,
  `total_exposure` bigint unsigned NOT NULL DEFAULT '0' COMMENT '整体曝光',
  `non_ad_exposure` bigint unsigned NOT NULL DEFAULT '0' COMMENT '非广告曝光',
  `ad_exposure` bigint unsigned NOT NULL DEFAULT '0' COMMENT '广告曝光',
  `ad_exposure_ratio_pct` decimal(7,4) NOT NULL DEFAULT '0.0000' COMMENT '广告曝光占整体曝光百分比',
  `ad_exposure_score` tinyint unsigned NOT NULL DEFAULT '0' COMMENT '0/50/100',
  `data_status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'NORMAL' COMMENT 'NORMAL/NO_EXPOSURE',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meituan_exposure_source` (`hotel_id`,`business_date`),
  KEY `idx_meituan_exposure_source_snapshot` (`hotel_id`,`snapshot_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='美团近30天流量来源曝光分析快照';

-- Table: meituan_ota_flow_conversion_30d
CREATE TABLE IF NOT EXISTS `meituan_ota_flow_conversion_30d` (
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `business_date` date NOT NULL,
  `period_start_date` date NOT NULL,
  `period_end_date` date NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `data_updated_at` datetime NOT NULL,
  `exposure_uv` bigint DEFAULT NULL,
  `browse_uv` bigint DEFAULT NULL,
  `pay_order_count` bigint DEFAULT NULL,
  `exposure_to_browse_rate_pct` decimal(9,4) DEFAULT NULL,
  `browse_to_pay_rate_pct` decimal(9,4) DEFAULT NULL,
  `peer_exposure_uv` bigint DEFAULT NULL,
  `peer_browse_uv` bigint DEFAULT NULL,
  `peer_pay_order_count` bigint DEFAULT NULL,
  `peer_exposure_to_browse_rate_pct` decimal(9,4) DEFAULT NULL,
  `peer_browse_to_pay_rate_pct` decimal(9,4) DEFAULT NULL,
  `exposure_peer_rank` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `browse_peer_rank` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `pay_order_peer_rank` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `exposure_to_browse_peer_rank` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `browse_to_pay_peer_rank` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`hotel_id`),
  KEY `idx_meituan_flow_period_end` (`hotel_id`,`period_end_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: meituan_ota_goods_price_mapping
CREATE TABLE IF NOT EXISTS `meituan_ota_goods_price_mapping` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `hotel_id` varchar(100) DEFAULT NULL,
  `ota_room_type_id` varchar(100) DEFAULT NULL,
  `room_type_name` varchar(255) DEFAULT NULL,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `business_date` date DEFAULT NULL,
  `ota_product_id` varchar(100) DEFAULT NULL,
  `ota_product_name` varchar(500) DEFAULT NULL,
  `rate_plan_name` varchar(500) DEFAULT NULL,
  `is_super_deal` tinyint(1) DEFAULT NULL,
  `ota_sale_price` decimal(10,2) DEFAULT NULL,
  `commission_rate` varchar(20) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_meituan_ota_goods_price_snapshot` (`snapshot_time`,`channel_source`),
  KEY `idx_meituan_ota_goods_price_business_date` (`business_date`),
  KEY `idx_meituan_ota_goods_price_goods` (`ota_product_id`),
  KEY `idx_meituan_ota_goods_price_room` (`ota_room_type_id`,`room_type_name`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_ota_joined_rights
CREATE TABLE IF NOT EXISTS `meituan_ota_joined_rights` (
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `right_id` bigint NOT NULL,
  `right_name` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  `rights_code` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `rights_content` text COLLATE utf8mb4_unicode_ci,
  `confirm_mode` varchar(40) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `effective_room_scope` varchar(150) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `today_stock` varchar(150) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `activity_names` text COLLATE utf8mb4_unicode_ci,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`hotel_id`,`right_id`),
  KEY `idx_meituan_joined_right_snapshot` (`hotel_id`,`snapshot_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='美团当前已报名权益快照';

-- Table: meituan_ota_nearby_event
CREATE TABLE IF NOT EXISTS `meituan_ota_nearby_event` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `hotel_id` varchar(100) DEFAULT NULL,
  `hotel_name` varchar(255) DEFAULT NULL,
  `poi_id` varchar(100) DEFAULT NULL,
  `event_id` varchar(100) NOT NULL,
  `event_class_id` int DEFAULT NULL,
  `event_name` varchar(500) DEFAULT NULL,
  `event_start_date` date DEFAULT NULL,
  `event_end_date` date DEFAULT NULL,
  `event_address` varchar(500) DEFAULT NULL,
  `distance_km` decimal(10,2) DEFAULT NULL,
  `countdown_days` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meituan_nearby_event` (`poi_id`,`event_id`),
  KEY `idx_meituan_nearby_event_snapshot` (`snapshot_time`),
  KEY `idx_meituan_nearby_event_date` (`event_start_date`,`event_end_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_ota_order_loss_monthly
CREATE TABLE IF NOT EXISTS `meituan_ota_order_loss_monthly` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  `business_date` date NOT NULL COMMENT '近30天统计窗口结束日期',
  `period_start_date` date NOT NULL,
  `period_end_date` date NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `total_loss_order_count` int NOT NULL,
  `total_loss_room_nights` int NOT NULL,
  `total_loss_amount` decimal(14,2) NOT NULL,
  `competitor_poi_id` bigint NOT NULL,
  `competitor_hotel_name` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL,
  `competitor_star` varchar(40) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `competitor_score` decimal(6,2) DEFAULT NULL,
  `competitor_lowest_price` decimal(12,2) DEFAULT NULL,
  `competitor_distance_m` int DEFAULT NULL,
  `competitor_circle_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `vip_tag` tinyint(1) NOT NULL DEFAULT '0',
  `follow_status` int DEFAULT NULL,
  `competitor_loss_order_count` int NOT NULL,
  `competitor_loss_order_ratio_pct` decimal(8,4) DEFAULT NULL,
  `competitor_loss_amount` decimal(14,2) NOT NULL,
  `lost_room_types_text` text COLLATE utf8mb4_unicode_ci,
  `lost_room_types_json` json DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meituan_order_loss_daily` (`hotel_id`,`business_date`,`competitor_poi_id`),
  KEY `idx_meituan_order_loss_date` (`hotel_id`,`business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='美团近30天流失订单竞争酒店分析';

-- Table: meituan_ota_promotion_activity
CREATE TABLE IF NOT EXISTS `meituan_ota_promotion_activity` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `hotel_id` varchar(100) DEFAULT NULL,
  `activity_source_type` varchar(100) DEFAULT NULL,
  `activity_id` varchar(100) DEFAULT NULL,
  `activity_name` varchar(255) DEFAULT NULL,
  `activity_status` varchar(100) DEFAULT NULL,
  `activity_time_range` varchar(255) DEFAULT NULL,
  `activity_rule_labels` text,
  `activity_room_type_count` int DEFAULT NULL,
  `activity_room_type_summary` text,
  PRIMARY KEY (`id`),
  KEY `idx_meituan_ota_promotion_snapshot` (`snapshot_time`,`channel_source`),
  KEY `idx_meituan_ota_promotion_activity` (`activity_id`,`activity_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_ota_promotion_performance_30d
CREATE TABLE IF NOT EXISTS `meituan_ota_promotion_performance_30d` (
  `hotel_id` varchar(64) NOT NULL,
  `period_start_date` date NOT NULL,
  `period_end_date` date NOT NULL,
  `snapshot_time` datetime NOT NULL,
  `plan_id` bigint unsigned NOT NULL,
  `plan_name` varchar(255) NOT NULL,
  `promotion_status` varchar(32) NOT NULL,
  `launch_id` bigint unsigned NOT NULL,
  `launch_name` varchar(255) NOT NULL,
  `promotion_name` varchar(128) NOT NULL,
  `promotion_type` int DEFAULT NULL,
  `shop_id` bigint unsigned DEFAULT NULL,
  `exposure_count` int DEFAULT NULL,
  `click_count` int DEFAULT NULL,
  `booking_order_count` int DEFAULT NULL,
  `room_night_count` int DEFAULT NULL,
  `booking_order_amount` decimal(14,2) DEFAULT NULL,
  `spend_amount` decimal(14,2) DEFAULT NULL,
  `cost_per_click` decimal(14,4) DEFAULT NULL,
  `click_rate_pct` decimal(10,4) DEFAULT NULL,
  `merchant_view_count` int DEFAULT NULL,
  `cash_spend_amount` decimal(14,2) DEFAULT NULL,
  PRIMARY KEY (`hotel_id`,`plan_id`,`launch_id`),
  KEY `idx_meituan_promotion_performance_period` (`hotel_id`,`period_start_date`,`period_end_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_ota_promotion_status
CREATE TABLE IF NOT EXISTS `meituan_ota_promotion_status` (
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `promotion_code` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `promotion_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/OPEN/CLOSED',
  PRIMARY KEY (`hotel_id`,`promotion_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Meituan promotion availability status';

-- Table: meituan_ota_review_detail
CREATE TABLE IF NOT EXISTS `meituan_ota_review_detail` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `hotel_id` varchar(100) DEFAULT NULL,
  `hotel_name` varchar(255) DEFAULT NULL,
  `poi_id` varchar(100) NOT NULL,
  `review_id` varchar(100) NOT NULL,
  `reviewer_name_masked` varchar(100) DEFAULT NULL,
  `review_score` decimal(10,2) DEFAULT NULL,
  `review_content` text,
  `review_time` datetime DEFAULT NULL,
  `stay_date` date DEFAULT NULL,
  `merchant_reply_content` text,
  `merchant_reply_time` datetime DEFAULT NULL,
  `is_replied` tinyint(1) DEFAULT NULL,
  `room_type_name` text,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `ota_product_name` text,
  `has_image` tinyint(1) DEFAULT NULL,
  `image_count` int DEFAULT NULL,
  `image_urls_json` longtext,
  `is_anonymous` tinyint(1) DEFAULT NULL,
  `is_negative_review` tinyint(1) DEFAULT NULL,
  `read_status` int DEFAULT NULL,
  `hygiene_score` decimal(10,2) DEFAULT NULL,
  `facility_score` decimal(10,2) DEFAULT NULL,
  `location_score` decimal(10,2) DEFAULT NULL,
  `service_score` decimal(10,2) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meituan_review_detail` (`channel_source`,`poi_id`,`review_id`),
  KEY `idx_meituan_review_detail_time` (`review_time`),
  KEY `idx_meituan_review_detail_status` (`is_negative_review`,`is_replied`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_ota_review_overview
CREATE TABLE IF NOT EXISTS `meituan_ota_review_overview` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `review_platform` varchar(20) NOT NULL DEFAULT 'meituan',
  `hotel_id` varchar(100) DEFAULT NULL,
  `review_score` decimal(10,2) DEFAULT NULL,
  `review_score_max` decimal(10,2) DEFAULT NULL,
  `environment_score` decimal(10,2) DEFAULT NULL,
  `facility_score` decimal(10,2) DEFAULT NULL,
  `service_score` decimal(10,2) DEFAULT NULL,
  `hygiene_score` decimal(10,2) DEFAULT NULL,
  `total_review_count` int DEFAULT NULL,
  `unreplied_review_count` int DEFAULT NULL,
  `negative_review_count` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_meituan_ota_review_overview_snapshot` (`snapshot_time`,`channel_source`),
  KEY `idx_meituan_review_overview_platform` (`hotel_id`,`review_platform`,`snapshot_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_ota_review_ranking
CREATE TABLE IF NOT EXISTS `meituan_ota_review_ranking` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `snapshot_time` datetime DEFAULT NULL,
  `channel_source` varchar(50) DEFAULT NULL,
  `hotel_id` varchar(100) DEFAULT NULL,
  `ranking_type` varchar(100) DEFAULT NULL,
  `ranking_position` int DEFAULT NULL,
  `rank_item_name` varchar(255) DEFAULT NULL,
  `rank_item_value` decimal(18,4) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_meituan_ota_review_ranking_snapshot` (`snapshot_time`,`channel_source`,`ranking_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_ota_scan_order_detail
CREATE TABLE IF NOT EXISTS `meituan_ota_scan_order_detail` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `order_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `scan_time` datetime DEFAULT NULL,
  `scan_source` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_type` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `order_status` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `check_in_time` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `real_pay_amount` decimal(12,2) DEFAULT NULL,
  `collected_at` datetime NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meituan_scan_order` (`hotel_id`,`order_id`),
  KEY `idx_meituan_scan_order_time` (`hotel_id`,`scan_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Meituan completed scan-to-order details, retained for the latest 30-day window';

-- Table: meituan_ota_user_source_monthly
CREATE TABLE IF NOT EXISTS `meituan_ota_user_source_monthly` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `business_date` date NOT NULL COMMENT '近30天统计窗口结束日期',
  `snapshot_time` datetime NOT NULL COMMENT '采集时间',
  `platform_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '美团',
  `local_user_pct` decimal(7,4) NOT NULL,
  `nonlocal_user_pct` decimal(7,4) NOT NULL,
  `new_user_pct` decimal(7,4) NOT NULL,
  `returning_user_pct` decimal(7,4) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meituan_user_source_daily` (`hotel_id`,`business_date`),
  KEY `idx_meituan_user_source_date` (`business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='美团近30天用户来源比例快照';

-- Table: meituan_ota_video_upload_status
CREATE TABLE IF NOT EXISTS `meituan_ota_video_upload_status` (
  `hotel_id` varchar(64) NOT NULL,
  `video_type` varchar(32) NOT NULL,
  `uploaded_count` int unsigned NOT NULL DEFAULT '0',
  `required_count` int unsigned NOT NULL DEFAULT '0',
  `status` varchar(16) NOT NULL,
  PRIMARY KEY (`hotel_id`,`video_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Table: meituan_price_task
CREATE TABLE IF NOT EXISTS `meituan_price_task` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '数据库内部主键',
  `task_id` varchar(64) DEFAULT NULL COMMENT '业务任务号',
  `hotel_id` varchar(100) NOT NULL DEFAULT '' COMMENT '酒店主 ID',
  `hotel_name` varchar(100) NOT NULL COMMENT '酒店展示名',
  `channel_source` varchar(32) NOT NULL DEFAULT 'meituan' COMMENT '渠道来源',
  `room_type_id` varchar(100) DEFAULT NULL COMMENT 'PMS/内部房型 ID',
  `room_type_name` varchar(200) NOT NULL COMMENT '房型名称',
  `ota_room_type_id` varchar(100) DEFAULT NULL COMMENT '美团侧房型 ID',
  `ota_product_id` varchar(100) NOT NULL COMMENT '美团商品 ID',
  `ota_product_name` varchar(200) DEFAULT NULL COMMENT '美团商品名称',
  `business_date` date NOT NULL COMMENT '售卖/入住日期',
  `current_sale_price` decimal(10,2) DEFAULT NULL COMMENT '当前售价',
  `target_sale_price` decimal(10,2) NOT NULL COMMENT '目标售价',
  `price_delta` decimal(10,2) DEFAULT NULL COMMENT '目标价减当前价',
  `price_delta_pct` decimal(10,4) DEFAULT NULL COMMENT '调价百分比',
  `execute_status` enum('PENDING','EXECUTING','SUCCESS','FAILED') NOT NULL DEFAULT 'PENDING' COMMENT '兼容旧插件的总执行状态',
  `review_status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT '人工审查状态',
  `plugin_status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT '插件处理状态',
  `verification_status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT '平台回查状态',
  `source_decision_id` varchar(128) DEFAULT NULL COMMENT '来源收益决策 ID',
  `approval_id` varchar(128) DEFAULT NULL COMMENT '审批 ID',
  `created_by` varchar(128) DEFAULT NULL COMMENT '创建者',
  `approved_by` varchar(128) DEFAULT NULL COMMENT '审批人',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `approved_at` datetime DEFAULT NULL COMMENT '审批时间',
  `queued_at` datetime DEFAULT NULL COMMENT '入队时间',
  `plugin_picked_at` datetime DEFAULT NULL COMMENT '插件领取时间',
  `executed_at` datetime DEFAULT NULL COMMENT '插件执行完成时间',
  `verified_at` datetime DEFAULT NULL COMMENT '平台回查完成时间',
  `platform_actual_price` decimal(10,2) DEFAULT NULL COMMENT '平台实际价格',
  `verification_message` varchar(512) DEFAULT NULL COMMENT '回查说明',
  `error_code` varchar(64) DEFAULT NULL COMMENT '错误码',
  `error_message` varchar(1000) DEFAULT NULL COMMENT '错误说明',
  `retry_count` int unsigned NOT NULL DEFAULT '0' COMMENT '重试次数',
  `last_retry_at` datetime DEFAULT NULL COMMENT '最近重试时间',
  `payload_json` json DEFAULT NULL COMMENT '创建任务上下文',
  `result_json` json DEFAULT NULL COMMENT '插件/回查结果',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_task_id` (`task_id`),
  KEY `idx_status` (`execute_status`),
  KEY `idx_workflow_status` (`review_status`,`plugin_status`,`verification_status`),
  KEY `idx_hotel_id` (`hotel_id`),
  KEY `idx_hotel_date` (`hotel_name`,`business_date`),
  KEY `idx_product_date` (`ota_product_id`,`business_date`),
  KEY `idx_source_decision` (`source_decision_id`),
  KEY `idx_approval` (`approval_id`),
  KEY `idx_created_at` (`created_at`),
  KEY `idx_task_lookup` (`hotel_id`,`hotel_name`,`channel_source`,`ota_product_id`,`business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='美团调价任务';

-- Table: pms_room_type_forecast
CREATE TABLE IF NOT EXISTS `pms_room_type_forecast` (
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '酒店内部 ID',
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '酒店名称',
  `source_platform` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PMS（别样红）' COMMENT '数据来源',
  `snapshot_time` datetime(6) NOT NULL COMMENT '预测采集时点',
  `stay_date` date NOT NULL COMMENT '预测入住日期',
  `room_type_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'PMS 房型名称',
  `room_type_id` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '系统统一房型 ID',
  `pms_room_type_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'PMS 房型标识',
  `total_rooms` decimal(18,4) DEFAULT NULL COMMENT '总房数',
  `available_rooms` decimal(18,4) DEFAULT NULL COMMENT '可售数',
  `occupied_rooms` decimal(18,4) DEFAULT NULL COMMENT '已售/在住数',
  `overbooking_rooms` decimal(18,4) DEFAULT NULL COMMENT '超预订数',
  `room_revenue` decimal(18,4) DEFAULT NULL COMMENT '预测房费',
  `adr` decimal(18,4) DEFAULT NULL COMMENT '平均房价',
  `revpar` decimal(18,4) DEFAULT NULL COMMENT 'RevPar',
  PRIMARY KEY (`hotel_id`,`stay_date`,`pms_room_type_id`),
  KEY `idx_forecast_stay_date` (`hotel_id`,`stay_date`),
  KEY `idx_forecast_room_type_id` (`hotel_id`,`room_type_id`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='PMS 房类预测快照';

-- Table: pms_room_type_hourly_status
CREATE TABLE IF NOT EXISTS `pms_room_type_hourly_status` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `hotel_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hotel_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `source_platform` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PMS',
  `snapshot_time` datetime(6) NOT NULL,
  `snapshot_hour` datetime NOT NULL,
  `stay_date` date NOT NULL,
  `room_type_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `room_type_id` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `pms_room_type_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `total_rooms` decimal(18,4) DEFAULT NULL,
  `available_rooms` decimal(18,4) DEFAULT NULL,
  `occupied_rooms` decimal(18,4) DEFAULT NULL,
  `overbooking_rooms` decimal(18,4) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_hourly_room_status` (`hotel_id`,`snapshot_hour`,`pms_room_type_id`),
  KEY `idx_hourly_status_day` (`hotel_id`,`stay_date`),
  KEY `idx_hourly_status_room_type` (`hotel_id`,`room_type_id`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table: rs01_room_revenue_daily
CREATE TABLE IF NOT EXISTS `rs01_room_revenue_daily` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `hotel_name` varchar(150) NOT NULL,
  `hotel_id` varchar(100) NOT NULL DEFAULT '',
  `source_platform` varchar(64) NOT NULL DEFAULT 'PMS（别样红）',
  `business_date` date NOT NULL,
  `room_no` varchar(50) NOT NULL,
  `room_type_name` varchar(120) DEFAULT NULL,
  `room_type_id` varchar(50) DEFAULT NULL COMMENT '系统统一房型ID，未映射时为空',
  `guest_name` varchar(100) DEFAULT NULL,
  `customer_source` varchar(100) DEFAULT NULL,
  `checkin_time` datetime DEFAULT NULL,
  `checkout_time` datetime DEFAULT NULL,
  `rack_rate` decimal(12,2) DEFAULT NULL,
  `price_type` varchar(100) DEFAULT NULL,
  `room_daily_price` decimal(12,2) DEFAULT NULL,
  `stay_type` varchar(50) DEFAULT NULL,
  `charge_subject` varchar(100) NOT NULL DEFAULT '',
  `room_nights` decimal(8,2) DEFAULT NULL,
  `room_fee` decimal(12,2) DEFAULT NULL,
  `operator_name` varchar(100) DEFAULT NULL,
  `order_id` varchar(100) NOT NULL DEFAULT '',
  `snapshot_time` datetime NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_room_fee_daily` (`hotel_id`,`order_id`,`business_date`,`room_no`,`charge_subject`),
  KEY `idx_hotel_id` (`hotel_id`),
  KEY `idx_business_date` (`business_date`),
  KEY `idx_room_no` (`room_no`),
  KEY `idx_order_id` (`order_id`),
  KEY `idx_snapshot_time` (`snapshot_time`),
  KEY `idx_hotel_room_type_id` (`hotel_id`,`room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- View: v_hotel_ota_operating_snapshot
CREATE OR REPLACE VIEW `v_hotel_ota_operating_snapshot` AS select `room_status`.`hotel_id` AS `hotel_id`,`room_status`.`business_date` AS `business_date`,cast(`room_status`.`snapshot_time` as time) AS `snapshot_at`,sum((case when (trim(`room_status`.`room_status`) in ('住净','住脏','空净','空脏')) then 1 else 0 end)) AS `sellable_rooms`,sum((case when (trim(`room_status`.`room_status`) in ('住净','住脏')) then 1 else 0 end)) AS `occupied_rooms`,cast(NULL as unsigned) AS `future_booked_rooms`,cast(NULL as unsigned) AS `extension_rooms`,sum((case when (trim(`room_status`.`room_status`) in ('维修','维修中','停用','锁房')) then 1 else 0 end)) AS `maintenance_rooms` from (`kf11_room_status_snapshot` `room_status` join (select `kf11_room_status_snapshot`.`hotel_id` AS `hotel_id`,`kf11_room_status_snapshot`.`business_date` AS `business_date`,max(`kf11_room_status_snapshot`.`snapshot_time`) AS `latest_snapshot_time` from `kf11_room_status_snapshot` where ((`kf11_room_status_snapshot`.`hotel_id` is not null) and (trim(`kf11_room_status_snapshot`.`hotel_id`) <> '') and (`kf11_room_status_snapshot`.`business_date` is not null) and (`kf11_room_status_snapshot`.`snapshot_time` is not null)) group by `kf11_room_status_snapshot`.`hotel_id`,`kf11_room_status_snapshot`.`business_date`) `latest` on(((`latest`.`hotel_id` = `room_status`.`hotel_id`) and (`latest`.`business_date` = `room_status`.`business_date`) and (`latest`.`latest_snapshot_time` = `room_status`.`snapshot_time`)))) where ((`room_status`.`hotel_id` is not null) and (trim(`room_status`.`hotel_id`) <> '')) group by `room_status`.`hotel_id`,`room_status`.`business_date`,cast(`room_status`.`snapshot_time` as time);

-- View: v_hotel_room_type_mapping_result
CREATE OR REPLACE VIEW `v_hotel_room_type_mapping_result` AS select `hotel_room_type_mapping`.`id` AS `id`,`hotel_room_type_mapping`.`hotel_id` AS `hotel_id`,`hotel_room_type_mapping`.`pms_hotel_name` AS `pms_hotel_name`,`hotel_room_type_mapping`.`room_type_id` AS `room_type_id`,`hotel_room_type_mapping`.`room_type_name` AS `room_type_name`,`hotel_room_type_mapping`.`pms_room_type_name` AS `pms_room_type_name`,`hotel_room_type_mapping`.`source_platform` AS `source_platform`,`hotel_room_type_mapping`.`ota_hotel_name` AS `ota_hotel_name`,`hotel_room_type_mapping`.`source_room_type_name` AS `source_room_type_name`,`hotel_room_type_mapping`.`ota_room_type_name` AS `ota_room_type_name`,`hotel_room_type_mapping`.`source_product_id` AS `source_product_id`,`hotel_room_type_mapping`.`source_product_name` AS `source_product_name`,`hotel_room_type_mapping`.`rate_plan_name` AS `rate_plan_name`,`hotel_room_type_mapping`.`product_cipher` AS `product_cipher`,`hotel_room_type_mapping`.`price_editable_flag` AS `price_editable_flag`,`hotel_room_type_mapping`.`is_hour_room` AS `is_hour_room`,`hotel_room_type_mapping`.`mapping_status` AS `mapping_status`,`hotel_room_type_mapping`.`match_rule` AS `match_rule`,`hotel_room_type_mapping`.`match_confidence` AS `match_confidence`,`hotel_room_type_mapping`.`reviewed_by` AS `reviewed_by`,`hotel_room_type_mapping`.`reviewed_at` AS `reviewed_at`,`hotel_room_type_mapping`.`review_note` AS `review_note`,`hotel_room_type_mapping`.`is_active` AS `is_active`,`hotel_room_type_mapping`.`first_seen_at` AS `first_seen_at`,`hotel_room_type_mapping`.`last_seen_at` AS `last_seen_at`,`hotel_room_type_mapping`.`created_at` AS `created_at`,`hotel_room_type_mapping`.`updated_at` AS `updated_at`,`hotel_room_type_mapping`.`remark` AS `remark` from `hotel_room_type_mapping` where ((`hotel_room_type_mapping`.`is_active` = 1) and (`hotel_room_type_mapping`.`source_product_id` <> ''));

SET FOREIGN_KEY_CHECKS = 1;
