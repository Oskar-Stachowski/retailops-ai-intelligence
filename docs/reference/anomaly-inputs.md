# AI07 — curated 1.2 and daily demand inputs

This slice extends the independent [snapshot 1.2 handoff](anomaly-snapshot-12.md)
into curated and a truth-free, reproducible demand feature artifact. It does not
qualify a detector or inherit an existing forecast/model approval.

## Curated boundary

Curated 1.2 uses source 2.8 and the reviewed snapshot 1.2 contract. All 43 operational
tables retain their native grains, exact decimal money, units, revisions and causal
availability. The parent source/snapshot/qualification IDs and transform fingerprints
are part of its identity. Inventory semantic verification regenerates each curated
row from persisted source fields, including its causal sale/return route. Any
quarantine row blocks ready publication. Existing curated 1.0 and 1.1 remain supported.

Private imports still require explicit truth opt-in at curation. Curated copies no
truth table or private scenario. Public and private parents have distinct IDs, while
their curated operational tables and demand feature bytes match exactly.

## Fit and scoring clocks

Each point is a UTC business day/product/selling location/channel. The fit cutoff is
one microsecond before that day begins. Scoring closes the full business day, then
waits a policy-defined delay (24 hours by default). This is offline daily detection.
It does not imply a live watermark or realtime detection.

Observed sales select the highest `daily_demand_versions` revision available by
scoring origin. Final `daily_demand_observations` quantities are never read by the
feature builder. A correction one microsecond after origin does not rewrite that
point. A higher revision available exactly at origin is visible. Ambiguous highest
versions are errors.

Expected sales use the same series seven days earlier, as known at fit cutoff.
There is no forecast artifact dependency. Fit uses only the preceding 28 calendar
days and excludes closed, missing and invalid observations. It requires at least
14 usable days, seven prior day-minus-seven residual pairs and the expected day's
observation. The evaluated day and later revisions cannot influence expected or
scale. Historical anomalies remain in training history; no oracle clean labels
filter them out.

Scale is `max(1 pcs, 1.4826 * MAD)` of those prior residuals, with MAD measured around
their median. The one-piece floor is recorded explicitly. This prevents division
by zero and is an input policy, not a tuned severity/alert threshold. The artifact
records observed/expected/residual units, scale and signed standardized residual.
Missing history, unavailable outcome, invalid input and closed days retain rows
and reasons without a residual or standardized residual. Explicit observed zero
remains a usable value.

## Context and isolation

The complete input table allowlist is `daily_demand_versions`, `price_plans`,
`promotion_plans` and `inventory_daily_snapshots`. Model features have their own
explicit allowlist in the manifest. Only the evaluator may later join truth labels.
There are no injection IDs, magnitudes, seeds, effect labels or producer model
parameters in feature rows. References contain operational row hashes and clocks.

Price and promotion context use the latest effective, scoped plans known before
window start. Price uses scope precedence; promotions use highest active priority.
A known promotion does not generate an alert. Missing plan price remains null.
Inventory is contextual observed information: the latest full snapshot for the
same product/physical stock/day available by scoring origin. Its reference is
marked `inventory_context`, separately from fit inputs; an unavailable stock
snapshot is null, while a known zero stock remains zero.

`dq_status` covers the already qualified canonical source and observation
availability only. The version-history contract records status/quantity, not raw
stream completeness. Every point therefore retains
`raw_dq_completeness=not_qualified`. The selected-sales raw DQ sample is insufficient
to certify full daily coverage. Returns are not converted from sparse events into
invented zero daily totals in this slice.

## Artifact and commands

```sh
uv sync --locked --extra snapshot --extra forecast
uv run --locked --extra snapshot retailops-ai-curated build \
  --import-dir "$IMPORT_DIR" --generated-root data/generated
uv run --locked --extra snapshot retailops-ai-anomaly-inputs build \
  --curated-dir "$CURATED_DIR" --generated-root data/generated
uv run --locked --extra snapshot retailops-ai-anomaly-inputs verify \
  --input-dir "$ANOMALY_INPUT_DIR" --curated-dir "$CURATED_DIR"
make anomaly-inputs-check
```

Outputs live under `data/generated/anomaly-inputs/<anomaly-input-id>` as typed,
canonical JSONL, a strict versioned manifest and its transport checksum. The
descriptor binds parent IDs, policy/hash, input/model allowlists, implementation,
packaged schemas, dependency lock, Python version, logical hash/count and statuses.
Scoring delay changes identity. Byte hashes guard transport separately.

Publication uses private staging, full parent replay, fsync and no-replace rename.
An identical rebuild verifies and reuses existing bytes. Verification requires
the original curated parent and the recorded implementation/schema/lock runtime;
it recomputes every point. Merely resealing a false residual's hashes is rejected.
Changing implementation requires a new artifact rather than silently accepting
an old artifact under a different formula.

Bounds are 100000 selected source rows/128 MiB canonical input, 100000 output
points/256 MiB output and 64 KiB per point. The selected operational tables are
loaded into memory after typed-digest reconciliation; this is a bounded offline
implementation, not a full-profile scaling qualification. The acceptance runner
uses two fresh isolated processes without the producer namespace, on both public
fixtures, with 300 seconds/1024 MiB per process. It checks stable IDs and unchanged
source/publication bytes. Schemas are packaged once in the wheel.

## Remaining AI07 work

This is the demand-input part of 07.4. Raw DQ integration with proven coverage,
return-focused inputs, label and temporal split manifests, frozen episode matching,
validation-selected seasonal baseline thresholds, Isolation Forest preprocessing,
held-out multi-seed observation/episode evaluation, champion selection and AI05
MLflow/batch/read-only serving remain open. The transport fixture has 30 days and
one seed per scenario family; it is not final model quality evidence. `ready_input`
means the demand residual can be computed, while detector readiness stays
`not_qualified` and curated anomaly readiness stays `not_ready`.

[Data card](../cards/anomaly-inputs.md) records the same scope and limits.
[Acceptance evidence](../evidence/ai/07/07.4-demand-inputs/README.md) pins the runtime,
artifact identities and measured fresh-process/wheel results.
