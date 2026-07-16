CREATE TABLE IF NOT EXISTS meituan_ota_joined_rights (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    hotel_id VARCHAR(64) NOT NULL,
    hotel_name VARCHAR(150) NOT NULL,
    right_id BIGINT NOT NULL,
    right_name VARCHAR(150) NOT NULL,
    rights_code VARCHAR(80) NOT NULL,
    rights_content TEXT NULL,
    confirm_mode VARCHAR(40) NULL,
    effective_room_scope VARCHAR(150) NULL,
    today_stock VARCHAR(150) NULL,
    activity_names TEXT NULL,
    snapshot_time DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_meituan_joined_right (hotel_id, right_id),
    KEY idx_meituan_joined_right_snapshot (hotel_id, snapshot_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='美团当前已报名权益快照';
