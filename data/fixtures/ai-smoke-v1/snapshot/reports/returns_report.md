# Chronology and returns quality

Policy: retail-returns-1.0.0. Status: passed.

| Check | Status | Sample size |
|---|---|---:|
| returns_schema | passed | 4300 |
| return_window_policy | passed | 32 |
| transaction_chronology | passed | 6096 |
| return_reference_and_window | passed | 1044 |
| return_quantity_and_refunds | passed | 1044 |
| return_snapshot_reconciliation | passed | 3224 |
| return_tail_completeness | passed | 1612 |

Return tail: 39 days. History events: 591; tail events: 453.
gross minus refunded amounts known at the explicit cutoff; final only after return-window and ingestion maturity
Inventory remains not ready.
