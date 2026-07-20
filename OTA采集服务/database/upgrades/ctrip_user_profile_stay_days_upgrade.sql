ALTER TABLE ctrip_ota_userprofile_distribution
  ADD COLUMN metric_value DECIMAL(12,4) NULL AFTER rate_pct,
  ADD COLUMN metric_unit VARCHAR(20) NULL AFTER metric_value;
