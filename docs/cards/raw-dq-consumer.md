# Data card — selected-sales DQ replay 1.0.0

Owner: RetailOps AI Intelligence. Synthetic operational capture/source binding is
checked against qualified source 2.8 → public snapshot 1.2 → curated 1.2. Five
operational tables, reviewed contracts and a narrow payload allowlist define the
boundary. Producer runtime, fault truth and expected replay are excluded.

Grain is UTC day/product/legacy store ID/channel/currency, whole pcs and exact
Decimal PLN/EUR without FX. Optional SKU does not define a business fact.
Duplicates do not double count; conflicting versions, unsupported fields,
premature availability and source mismatches are quarantined. Late/out-of-order
sales append revisions visible only from received time. Missing grains stay absent.

Each frozen single-seed, 30-day sample selects 256 sales: 258 deliveries and
29 progress declarations yield 253 accepted facts, two duplicates and three
quarantine/offline-DLQ records. Independent operational results match the accepted
producer output. Progress qualifies only the selected stream; daily completeness,
model readiness and transport durability remain unqualified. Reports are offline
audit metadata, not fitted features or anomaly scores. Coverage joins, returns,
detector evaluation and lifecycle remain open.

[Runbook and limits](../reference/raw-dq-consumer.md).
