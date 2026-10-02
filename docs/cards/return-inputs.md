# Return event-day operational view — 1.0.0

- Purpose: audit returns known at one explicit UTC origin, retaining the tail after
  sales history and purchase lifecycle changes. It is not a model training dataset.
- Parents: verified curated 1.2 from source 2.8 / snapshot 1.2. The independent
  consumer does not import RetailOps generator code or use injection labels.
- Grain: return UTC day / product / original selling location / channel / currency.
  Scope: purchases in the parent source only; no pre-history carry-in assertion.
- Values: known event count, refunded units, rejected units, exact refund amount,
  operational membership digest, member availability and original stock locations.
- Temporal boundary: explicit as-of origin, including causal purchase/catalog/policy
  availability. Query policy and origin are part of the immutable identity.
- Missingness: observed return units remain null. No visible member is not evidence
  of complete zero. Purchase cohort maturity does not establish event-day coverage.
- Quality: every point is `insufficient_data` with return coverage `not_qualified`.
  Model feature allowlist is empty; no score, alert, detector fit or promotion.
- Bounds: 366 inclusive days; 100,000 input/output rows; 128 MiB input/output;
  64 KiB per output row. Independent process acceptance: 300 s / 1,024 MiB RSS.
- Integrity: full semantic replay against the operational parent, exact artifact
  allowlist, runtime/schema/lock fingerprint, staged fsync and no-replace publication.
- Remaining gate: versioned full-stream DQ and operational return-event-day coverage
  before complete observations or anomaly scoring.

[Runbook](../reference/return-inputs.md) and
[acceptance evidence](../evidence/ai/07/07.4-return-inputs/README.md).
