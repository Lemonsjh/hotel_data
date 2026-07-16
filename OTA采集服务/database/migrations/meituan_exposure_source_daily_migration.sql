CREATE TABLE IF NOT EXISTS meituan_ota_exposure_source_daily (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    hotel_id VARCHAR(64) NOT NULL,
    business_date DATE NOT NULL,
    snapshot_time DATETIME NOT NULL,
    total_exposure BIGINT UNSIGNED NOT NULL DEFAULT 0,
    non_ad_exposure BIGINT UNSIGNED NOT NULL DEFAULT 0,
    ad_exposure BIGINT UNSIGNED NOT NULL DEFAULT 0,
    ad_exposure_ratio_pct DECIMAL(7,4) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_meituan_exposure_source_daily (hotel_id, business_date),
    KEY idx_meituan_exposure_source_daily_snapshot (hotel_id, snapshot_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Meituan daily exposure-source detail';
