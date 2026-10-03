# Data card — anomaly demand inputs 1.0.0

Owner: RetailOps AI Intelligence. Inputs are synthetic operational facts/plans from
qualified source 2.8 → snapshot 1.2 → curated 1.2. Grain is UTC business
day/product/selling location/channel. Observed quantity means sold saleable pcs,
including inventory censoring; it is not oracle unconstrained demand.

The immutable descriptor carries source/snapshot/qualification/curated IDs,
policy, implementation, lock/schema hashes and canonical feature hash/counts.
Four operational tables are allowlisted. Model feature columns are explicit;
labels and injection configuration are excluded. Historical corrections are
selected as-of, with separate fit cutoff and scoring origin. Closed/missing/invalid
inputs retain their reason and do not become scored business drops. Price stays
an exact decimal string plus currency, without FX or pack conversion.

Seasonal-naive expected and robust residual scale use only history known before
the evaluated day. MAD zero uses a recorded one-piece floor. Price/promotion
context is known before window start; inventory context is observed by scoring
origin. Promotion itself is not an alert. Historical anomaly labels do not filter
fit history. Raw stream completeness remains explicitly not qualified.

Frozen transport fixtures cover demand and physical scenario families, with
30 days and one seed. They verify mechanics and isolation, not precision/recall,
three-seed model quality or a serving-ready champion. This version covers demand
residual inputs; return daily coverage, DQ integration, evaluation labels/splits,
baseline/IF fitting and lifecycle qualification remain later work.

[Runbook and resource limits](../reference/anomaly-inputs.md).
