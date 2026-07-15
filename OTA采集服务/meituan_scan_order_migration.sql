CREATE TABLE IF NOT EXISTS meituan_ota_scan_order_detail (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    hotel_id VARCHAR(64) NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    scan_time DATETIME NULL,
    scan_source VARCHAR(64) NULL,
    user_type VARCHAR(64) NULL,
    order_status VARCHAR(64) NULL,
    check_in_time VARCHAR(64) NULL,
    real_pay_amount DECIMAL(12,2) NULL,
    collected_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_meituan_scan_order (hotel_id, order_id),
    KEY idx_meituan_scan_order_time (hotel_id, scan_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Meituan completed scan-to-order details, retained for the latest 30-day window';
