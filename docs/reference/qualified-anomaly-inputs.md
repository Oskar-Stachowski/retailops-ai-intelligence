# Receipt-qualified anomaly inputs 1.0

`qualified-anomaly-inputs-1.0.0` adds sales and return event-day residual inputs
after [day qualification](day-qualification.md). It preserves the earlier
`anomaly-demand-inputs-1.0.0` contract and all source/snapshot/curated/DQ IDs.
The grain is `(event_type, business_date, product_id, selling_location_id,
channel, currency)`. Currency and event type are never pooled.
The artifact follows the explicit parent declaration inventory. A requested grain
without a declaration remains `no_declaration`; absence from this inventory is
not a complete observation or a clean negative. Evaluation must also account
for requested but undeclared/unscoreable windows in its coverage denominators.

## Knowledge and values

Every output contains the evaluated day's qualified observation and exactly 28
historical day queries, including their unknown/closed/DQ reasons. Historical
queries use **UTC day start minus one microsecond** as their shared fit cutoff.
The evaluated day uses **exclusive UTC day end plus 24 hours for sales or 72
hours for returns**, inclusive at that exact instant. Both delays are configurable
from 0 to 168 hours and bound to the artifact policy. The scoring clock is the
day gate's clock; the previous demand-only view's minus-one-microsecond scoring
clock remains unchanged in that older contract.

Only receipt-qualified observed units enter the fit. A closure or receipt arriving
after the fit cutoff cannot rewrite that history, even if it is known by scoring
time. Missing declarations, unavailable closure, closed locations, incomplete
source, missing accepted facts and unattributable quarantine remain unknown.
They are excluded from fit and never become zero. An explicit zero requires
successful day qualification. Native replacements repair only later views.

Returns use accepted refunded units; rejected quantities remain in the separate
`observation.rejected_units` audit field. Return closure covers only purchases
in the verified finite synthetic parent source. It does not establish global
return-day closure, carry-in completeness or cohort maturity.

The expected value is the same series' qualified day minus seven. The fit needs
at least 14 usable days and seven prior seasonal residual pairs within the 28-day
window. Scale is `max(1, 1.4826 * MAD)` of those prior residuals around their
median. The evaluated quantity cannot affect expected value or scale. A residual
exists only when both history and outcome qualify. `ready_input` means those
arithmetic inputs exist; detector readiness remains `not_qualified`.
`model_row(point)` exposes only the manifest's fixed numeric allowlist:
observed/expected/residual units, robust scale, standardized residual, planned
price, offered-promotion flag and on-hand. It returns no row for unready inputs.
Query audit, IDs, timestamps, reasons, rejected units, labels and injection
parameters stay outside this model interface.

## Context

Price and promotion plans must be known by the fit cutoff and effective on the
evaluated day. Price selection also matches the series' currency. A known
promotion is context and does not create an alert. The daily demand version may
supply a stock-location mapping known by scoring; its aggregate quantity and
quality never supply fit or outcome values. Stock snapshots must be known by
scoring, describe the evaluated business day and declare full-day known status.
Context record hashes and availability times are retained.

Zero on-hand is reported as `potential_stockout`; missing stock is `unavailable`.
Positive on-hand is only `no_stockout_signal`, not proof of uncensored demand.
This input stage does not impute censored demand or approve model scoring.

## Build and independently verify

```sh
retailops-ai-qualified-anomaly-inputs build \
  --replay-dir /path/to/full-dq-replay \
  --coverage-dir /path/to/day-coverage \
  --curated-dir /path/to/curated \
  --import-dir /path/to/snapshot \
  --generated-root /separate/workspace/data/generated

retailops-ai-qualified-anomaly-inputs verify \
  --directory /separate/workspace/data/generated/qualified-anomaly-inputs/ID \
  --replay-dir /path/to/full-dq-replay \
  --coverage-dir /path/to/day-coverage \
  --curated-dir /path/to/curated \
  --import-dir /path/to/snapshot

make qualified-anomaly-inputs-check
```

The builder re-verifies native source → snapshot → curated → full DQ and
publisher closure semantics, then loads bounded verified context tables. It
queries the day gate at the two required clocks rather than treating a flattened
`qualified_days.jsonl` scoring view as training history. Its manifest binds the
full-DQ descriptor, coverage, policy, qualification/context implementation,
packaged contracts, Python patch version and exact output bytes. Verification
reconstructs every output from these parents, so changing content and refreshing
both seals cannot substitute different observations or context.

Artifacts contain exactly `features.jsonl`, `feature_manifest.json` and
`manifest.sha256`; publication is atomic and immutable. Parent overlap,
symlinks, hardlinks, changed reads and unbounded rows/files are rejected. Output
is bounded to 10,000 rows / 128 MiB. The required check runs two fresh isolated
processes, each with both public profiles, unchanged inputs and identical artifact
bytes. Each process retains the 300-second / 1,024-MiB budget. There are no
producer namespaces or private evaluation truth in these processes.

The public 30-day, single-seed transport fixtures demonstrate input mechanics,
not detector quality. [Development detectors](anomaly-detectors.md) now implement
validation-capacity thresholds, train-only preprocessing and portable Isolation
Forest mechanics. AI07 still needs model comparison, frozen observation/episode
evaluation across scenarios/seeds and its detector lifecycle/publication gate.
