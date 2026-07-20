CREATE TABLE IF NOT EXISTS meituan_ota_promotion_status (
    hotel_id VARCHAR(64) NOT NULL,
    promotion_code VARCHAR(64) NOT NULL,
    promotion_name VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/OPEN/CLOSED',
    PRIMARY KEY (hotel_id, promotion_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Meituan promotion availability status';
