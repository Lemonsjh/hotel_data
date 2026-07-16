CREATE TABLE IF NOT EXISTS meituan_ota_user_source_monthly (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    hotel_id VARCHAR(64) NOT NULL,
    business_date DATE NOT NULL COMMENT '近30天统计窗口结束日期',
    snapshot_time DATETIME NOT NULL COMMENT '采集时间',
    platform_type VARCHAR(20) NOT NULL DEFAULT '美团',
    local_user_pct DECIMAL(7,4) NOT NULL,
    nonlocal_user_pct DECIMAL(7,4) NOT NULL,
    new_user_pct DECIMAL(7,4) NOT NULL,
    returning_user_pct DECIMAL(7,4) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_meituan_user_source_daily (hotel_id, business_date),
    KEY idx_meituan_user_source_date (business_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='美团近30天用户来源比例快照';
