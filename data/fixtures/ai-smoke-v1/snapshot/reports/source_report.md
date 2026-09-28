# Source acceptance

Policy: forecast-source-acceptance-1.1.0. Status: passed.

| Check | Status | Sample | Value | Threshold | Evidence |
|---|---|---:|---|---|---|
| legacy:primary_keys_are_unique | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:sales_reference_products | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:orders_reference_stores | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:order_items_reference_orders_and_products | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:pricing_references_products | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:inventory_references_products_and_warehouses | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:stock_movements_reference_products_and_warehouses | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:returns_reference_orders_order_items_and_products | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:operational_records_reference_valid_entities | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:sales_values_are_positive | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:inventory_and_returns_are_non_negative | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:order_totals_match_order_items | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:returns_do_not_exceed_order_item_quantity | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:date_windows_are_ordered | passed | 39992 | 0 | 0 | quality_report.json |
| legacy:sales_data_quality_statuses_are_known | passed | 39992 | 0 | 0 | quality_report.json |
| dimension_schema_pk_sku | passed | 488 | 0 | 0 | dimensions_report.json |
| selling_stock_channel_region | passed | 3 | 0 | 0 | dimensions_report.json |
| assignment_routing_versions | passed | 6 | 0 | 0 | dimensions_report.json |
| catalog_lifecycle_assortment | passed | 1612 | 0 | 0 | dimensions_report.json |
| calendar_exact_coverage | passed | 90 | 0 | 0 | dimensions_report.json |
| category_season_coverage | passed | 240 | 0 | 0 | dimensions_report.json |
| legacy_adapter_consistency | passed | 25 | 0 | 0 | dimensions_report.json |
| sales_active_open_known_routing | passed | 6096 | 0 | 0 | dimensions_report.json |
| pricing_schema_versions_scope | passed | 7606 | 0 | 0 | pricing_report.json |
| known_price_coverage_and_priority | passed | 1612 | 0 | 0 | pricing_report.json |
| transaction_price_reconciliation | passed | 6096 | 0 | 0 | pricing_report.json |
| realized_price_aggregates | passed | 1292 | 0 | 0 | pricing_report.json |
| legacy_pricing_adapter | passed | 122 | 0 | 0 | pricing_report.json |
| promotion_truth_direction | passed | 96 | 0 | 0 | pricing_report.json |
| demand_schema | passed | 3269 | 0 | 0 | demand_report.json |
| daily_panel_coverage | passed | 1612 | 0 | 0 | demand_report.json |
| daily_transaction_aggregation | passed | 1612 | 0 | 0 | demand_report.json |
| basket_sku_totals | passed | 3244 | 0 | 0 | demand_report.json |
| daily_demand_budget | passed | 1469 | 0 | 0 | demand_report.json |
| daily_source_completeness | passed | 1612 | 0 | 0 | demand_report.json |
| returns_schema | passed | 4300 | 0 | 0 | returns_report.json |
| return_window_policy | passed | 32 | 0 | 0 | returns_report.json |
| transaction_chronology | passed | 6096 | 0 | 0 | returns_report.json |
| return_reference_and_window | passed | 1044 | 0 | 0 | returns_report.json |
| return_quantity_and_refunds | passed | 1044 | 0 | 0 | returns_report.json |
| return_snapshot_reconciliation | passed | 3224 | 0 | 0 | returns_report.json |
| return_tail_completeness | passed | 1612 | 0 | 0 | returns_report.json |
| fact_schema_without_simulation | passed | 39992 | 0 | 0 | source_report.json |
| simulation_parameters_schema_coverage | passed | 23 | 0 | 0 | source_report.json |
| feature_fact_projection | passed | 1612 | 0 | 0 | source_report.json |
| append_only_observation_history | passed | 1612 | 0 | 0 | source_report.json |

| Use case | Status | Reason |
|---|---|---|
| forecast_source | ready | AI-03 snapshot/curated input; observed_sales_units only |
| forecasting | not_ready | AI-03/04 snapshot, training and fixed-origin assessment required |
| anomaly | not_ready | AI-05 labels and evaluation required |
| stockout | not_ready | AI-06 inventory ledger and reliable availability required |
| replay | not_ready | AI-03/04 historical correction snapshots required |
| rag | not_applicable | Source sales qualification does not qualify RAG |
