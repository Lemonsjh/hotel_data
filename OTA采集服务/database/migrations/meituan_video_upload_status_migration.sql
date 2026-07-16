CREATE TABLE IF NOT EXISTS meituan_ota_video_upload_status (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    hotel_id VARCHAR(64) NOT NULL,
    video_type VARCHAR(32) NOT NULL,
    uploaded_count INT UNSIGNED NOT NULL DEFAULT 0,
    required_count INT UNSIGNED NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_meituan_video_upload_status (hotel_id, video_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
