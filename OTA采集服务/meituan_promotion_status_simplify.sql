DROP INDEX idx_meituan_promotion_status_check ON meituan_ota_promotion_status;

ALTER TABLE meituan_ota_promotion_status
    DROP COLUMN max_score,
    DROP COLUMN is_sellable,
    DROP COLUMN checked_at,
    DROP COLUMN source_url,
    DROP COLUMN remark,
    DROP COLUMN created_at,
    DROP COLUMN updated_at;
