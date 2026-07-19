ALTER TABLE ctrip_ota_flow_conversion_30d
  ADD COLUMN detail_to_order_rate_pct DECIMAL(9,4) NULL AFTER order_submit_count,
  ADD COLUMN order_to_submit_rate_pct DECIMAL(9,4) NULL AFTER detail_to_order_rate_pct,
  ADD COLUMN peer_detail_to_order_rate_pct DECIMAL(9,4) NULL AFTER peer_order_submit_count,
  ADD COLUMN peer_order_to_submit_rate_pct DECIMAL(9,4) NULL AFTER peer_detail_to_order_rate_pct;

UPDATE ctrip_ota_flow_conversion_30d
SET detail_to_order_rate_pct=CASE
      WHEN detail_exposure IS NULL OR detail_exposure=0 THEN NULL
      ELSE ROUND(order_filling_count / detail_exposure * 100, 4)
    END,
    order_to_submit_rate_pct=CASE
      WHEN order_filling_count IS NULL OR order_filling_count=0 THEN NULL
      ELSE ROUND(order_submit_count / order_filling_count * 100, 4)
    END,
    peer_detail_to_order_rate_pct=CASE
      WHEN peer_detail_exposure IS NULL OR peer_detail_exposure=0 THEN NULL
      ELSE ROUND(peer_order_filling_count / peer_detail_exposure * 100, 4)
    END,
    peer_order_to_submit_rate_pct=CASE
      WHEN peer_order_filling_count IS NULL OR peer_order_filling_count=0 THEN NULL
      ELSE ROUND(peer_order_submit_count / peer_order_filling_count * 100, 4)
    END;
