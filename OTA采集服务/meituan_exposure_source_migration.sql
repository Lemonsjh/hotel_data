CREATE TABLE IF NOT EXISTS meituan_ota_exposure_source_monthly (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    hotel_id VARCHAR(64) NOT NULL,
    business_date DATE NOT NULL COMMENT '近30天统计窗口结束日',
    snapshot_time DATETIME NOT NULL,
    total_exposure BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '整体曝光',
    non_ad_exposure BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '非广告曝光',
    ad_exposure BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '广告曝光',
    ad_exposure_ratio_pct DECIMAL(7,4) NOT NULL DEFAULT 0 COMMENT '广告曝光占整体曝光百分比',
    ad_exposure_score TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '0/50/100',
    data_status VARCHAR(24) NOT NULL DEFAULT 'NORMAL' COMMENT 'NORMAL/NO_EXPOSURE',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_meituan_exposure_source (hotel_id, business_date),
    KEY idx_meituan_exposure_source_snapshot (hotel_id, snapshot_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='美团近30天流量来源曝光分析快照';
