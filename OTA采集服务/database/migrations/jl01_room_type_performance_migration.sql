CREATE TABLE IF NOT EXISTS `jl01_room_type_performance_daily` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '内部主键',
    `hotel_id` VARCHAR(64) NOT NULL COMMENT '酒店内部 ID',
    `hotel_name` VARCHAR(255) NOT NULL COMMENT '酒店名称',
    `source_platform` VARCHAR(32) NOT NULL DEFAULT 'PMS（别样红）' COMMENT '数据来源',
    `business_date` DATE NOT NULL COMMENT '营业日',
    `room_type_name` VARCHAR(255) NOT NULL COMMENT 'PMS 房型名称',
    `room_type_id` VARCHAR(50) NULL COMMENT '系统统一房型 ID',
    `pms_rate_room_type_id` VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'JL01 费率房型标识',
    `room_nights` DECIMAL(18,4) NULL COMMENT '间夜数',
    `occupancy_rate` DECIMAL(9,4) NULL COMMENT '出租率，百分比数值',
    `room_revenue` DECIMAL(18,4) NULL COMMENT '房费',
    `adr` DECIMAL(18,4) NULL COMMENT '平均房价',
    `revpar` DECIMAL(18,4) NULL COMMENT 'RevPar',
    `snapshot_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '采集时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_jl01_hotel_date_room` (`hotel_id`, `business_date`, `room_type_name`),
    KEY `idx_jl01_room_type_id` (`hotel_id`, `room_type_id`),
    KEY `idx_jl01_business_date` (`hotel_id`, `business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='PMS JL01 按房型实际经营日报';
