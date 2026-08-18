RENAME TABLE `meituan_ota_business_metrics_hourly`
    TO `meituan_ota_business_metrics_hourly_wide_legacy`;

CREATE TABLE `meituan_ota_business_metrics_hourly` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `hotel_id` VARCHAR(100) NOT NULL,
    `hotel_name` VARCHAR(255) NULL,
    `business_date` DATE NOT NULL,
    `snapshot_time` DATETIME NOT NULL,
    `snapshot_hour` DATETIME NOT NULL,
    `metric_code` VARCHAR(64) NOT NULL,
    `metric_name` VARCHAR(100) NOT NULL,
    `metric_value` DECIMAL(18,4) NULL,
    `metric_unit` VARCHAR(50) NULL,
    `competitor_rank` VARCHAR(100) NULL COMMENT '同行排名',
    `peer_average` VARCHAR(100) NULL COMMENT '同行均值',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_meituan_business_hourly` (`hotel_id`, `snapshot_hour`, `metric_code`),
    KEY `idx_meituan_business_hourly_date` (`hotel_id`, `business_date`),
    KEY `idx_meituan_business_hourly_snapshot` (`snapshot_time`),
    KEY `idx_meituan_business_hourly_metric` (`hotel_id`, `metric_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO `meituan_ota_business_metrics_hourly`
    (`hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, `metric_code`, `metric_name`, `metric_value`, `metric_unit`)
SELECT `hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, 'DAY_ROOM_LOWEST_PRICE_AVG', '引流价', `traffic_price`, '元'
FROM `meituan_ota_business_metrics_hourly_wide_legacy`
UNION ALL SELECT `hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, 'EXPOSE_PV_CNT', '曝光量', `exposure_count`, '次'
FROM `meituan_ota_business_metrics_hourly_wide_legacy`
UNION ALL SELECT `hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, 'INTENTION_UV', '浏览人数', `browse_count`, '人'
FROM `meituan_ota_business_metrics_hourly_wide_legacy`
UNION ALL SELECT `hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, 'PAY_ORDER_CNT_UV', '支付转化率', `payment_conversion_rate`, '%'
FROM `meituan_ota_business_metrics_hourly_wide_legacy`
UNION ALL SELECT `hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, 'PAY_ORDER_CNT', '支付订单数', `payment_order_count`, '单'
FROM `meituan_ota_business_metrics_hourly_wide_legacy`
UNION ALL SELECT `hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, 'PAY_ROOMNIGHT', '销售间夜', `sales_room_nights`, '间夜'
FROM `meituan_ota_business_metrics_hourly_wide_legacy`
UNION ALL SELECT `hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, 'PAY_ADR', '销售均价', `sales_average_price`, '元'
FROM `meituan_ota_business_metrics_hourly_wide_legacy`
UNION ALL SELECT `hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, 'PAY_AMT', '销售额', `sales_amount`, '元'
FROM `meituan_ota_business_metrics_hourly_wide_legacy`
UNION ALL SELECT `hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, 'CONSUME_ROOMNIGHT_SPLIT_EX_7DAYS_REFUND', '入住间夜', `checkin_room_nights`, '间夜'
FROM `meituan_ota_business_metrics_hourly_wide_legacy`
UNION ALL SELECT `hotel_id`, `hotel_name`, `business_date`, `snapshot_time`, `snapshot_hour`, 'NOT_AVAILABLE_REAL_ROOM_RATE', '满房率', `occupancy_rate`, '%'
FROM `meituan_ota_business_metrics_hourly_wide_legacy`;
