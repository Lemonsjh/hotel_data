UPDATE `meituan_ota_business_metrics`
SET `metric_value` = `metric_value` * 100
WHERE `metric_unit` = '%'
  AND `metric_value` BETWEEN 0 AND 1;

UPDATE `meituan_ota_business_metrics_hourly`
SET `metric_value` = `metric_value` * 100
WHERE `metric_unit` = '%'
  AND `metric_value` BETWEEN 0 AND 1;
