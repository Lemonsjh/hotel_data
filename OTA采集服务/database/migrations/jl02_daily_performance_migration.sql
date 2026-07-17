-- JL02 酒店经营业绩日报（本日 / 本月 / 本年累计）
CREATE TABLE IF NOT EXISTS `jl02_hotel_performance_daily` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '内部主键',
    `hotel_id` VARCHAR(64) NOT NULL COMMENT '酒店内部 ID',
    `hotel_name` VARCHAR(255) NOT NULL COMMENT '酒店名称',
    `source_platform` VARCHAR(32) NOT NULL DEFAULT 'PMS（别样红）' COMMENT '数据来源',
    `business_date` DATE NOT NULL COMMENT 'PMS 营业日',
    `category` VARCHAR(64) NOT NULL COMMENT '报表类别',
    `metric_name` VARCHAR(128) NOT NULL COMMENT '统计项目',
    `value_day` DECIMAL(18,4) NULL COMMENT '本日值，百分比按数值保存',
    `value_month` DECIMAL(18,4) NULL COMMENT '本月累计值，百分比按数值保存',
    `value_year` DECIMAL(18,4) NULL COMMENT '本年累计值，百分比按数值保存',
    `snapshot_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '采集时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_jl02_hotel_date_metric` (
        `hotel_id`, `business_date`, `category`, `metric_name`
    ),
    KEY `idx_jl02_business_date` (`hotel_id`, `business_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='PMS JL02 酒店经营业绩日报';
