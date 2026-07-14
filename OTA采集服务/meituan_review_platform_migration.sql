SET @has_column := (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'meituan_ota_review_overview'
      AND COLUMN_NAME = 'review_platform'
);
SET @sql := IF(@has_column = 0,
    'ALTER TABLE meituan_ota_review_overview ADD COLUMN review_platform VARCHAR(20) NOT NULL DEFAULT ''meituan'' AFTER channel_source',
    'SELECT 1'
);
PREPARE review_platform_column FROM @sql;
EXECUTE review_platform_column;
DEALLOCATE PREPARE review_platform_column;

UPDATE meituan_ota_review_overview
SET review_platform = 'meituan'
WHERE review_platform IS NULL OR review_platform = '';

SET @has_index := (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'meituan_ota_review_overview'
      AND INDEX_NAME = 'idx_meituan_review_overview_platform'
);
SET @sql := IF(@has_index = 0,
    'CREATE INDEX idx_meituan_review_overview_platform ON meituan_ota_review_overview (hotel_id, review_platform, snapshot_time)',
    'SELECT 1'
);
PREPARE review_platform_index FROM @sql;
EXECUTE review_platform_index;
DEALLOCATE PREPARE review_platform_index;
