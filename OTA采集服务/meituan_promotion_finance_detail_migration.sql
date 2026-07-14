CREATE TABLE IF NOT EXISTS meituan_ota_promotion_finance_detail (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    hotel_id VARCHAR(64) NOT NULL,
    record_id BIGINT UNSIGNED NOT NULL,
    transaction_time VARCHAR(64) NOT NULL,
    product_type VARCHAR(128) NOT NULL,
    transaction_type VARCHAR(128) NOT NULL,
    transaction_amount DECIMAL(12,2) NOT NULL,
    balance DECIMAL(12,2) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_meituan_promotion_finance_record (hotel_id, record_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
