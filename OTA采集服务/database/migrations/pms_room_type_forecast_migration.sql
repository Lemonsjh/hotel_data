CREATE TABLE IF NOT EXISTS `pms_room_type_forecast` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '内部主键',
    `hotel_id` VARCHAR(64) NOT NULL COMMENT '酒店内部 ID',
    `hotel_name` VARCHAR(255) NOT NULL COMMENT '酒店名称',
    `source_platform` VARCHAR(32) NOT NULL DEFAULT 'PMS（别样红）' COMMENT '数据来源',
    `snapshot_time` DATETIME(6) NOT NULL COMMENT '预测采集时点',
    `stay_date` DATE NOT NULL COMMENT '预测入住日期',
    `room_type_name` VARCHAR(255) NOT NULL COMMENT 'PMS 房型名称',
    `room_type_id` VARCHAR(50) NULL COMMENT '系统统一房型 ID',
    `pms_room_type_id` VARCHAR(64) NOT NULL COMMENT 'PMS 房型标识',
    `total_rooms` DECIMAL(18,4) NULL COMMENT '总房数',
    `available_rooms` DECIMAL(18,4) NULL COMMENT '可售数',
    `occupied_rooms` DECIMAL(18,4) NULL COMMENT '已售/在住数',
    `overbooking_rooms` DECIMAL(18,4) NULL COMMENT '超预订数',
    `room_revenue` DECIMAL(18,4) NULL COMMENT '预测房费',
    `adr` DECIMAL(18,4) NULL COMMENT '平均房价',
    `revpar` DECIMAL(18,4) NULL COMMENT 'RevPar',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_forecast_current` (`hotel_id`, `stay_date`, `pms_room_type_id`),
    KEY `idx_forecast_stay_date` (`hotel_id`, `stay_date`),
    KEY `idx_forecast_room_type_id` (`hotel_id`, `room_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='PMS 房类预测快照';
