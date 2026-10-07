# AI07 — independent selected-sales DQ replay

The boundary takes only `raw/events.jsonl`, `source_binding.json` and a verified
curated 1.2 parent. Extra files, including fault truth, are rejected. Normal
publication imports no producer module, plan, labels or expected replay.

The source binding is independently checked against source ID/descriptor hash,
sales count, chronological equally spaced selection with inclusive endpoints
and selected canonical event hash. Projection uses sales, orders, sale price
references, product catalog and native inventory sales. It preserves reviewed
legacy identity and causal availability. Seed is only event identity/provenance,
never a model feature. This projects existing facts; it does not regenerate the
producer process. The low-level reducer can test protocol cases without a parent;
publication always verifies the parent and binding.

Only event version `1.0` is accepted. Packaged OPS07 JSON Schema and a narrower
operational payload allowlist reject unsupported versions/additive fields and
legacy optional `latent_demand`, `observed_sales`, `data_quality_status`. Missing
SKU is valid and does not change business identity. JSON rejects duplicate keys,
nonfinite numbers and non-object values. Quantity is positive whole pcs, money
is exact decimal to two places, currency PLN/EUR, and price times quantity equals
amount when price is present. New facts must match the canonical source and be
available at delivery. Reasons/body hashes in quarantine do not expose payloads.

Same event ID/content is an exact duplicate; same source/sale ID/business version
is a business duplicate. Neither adds another fact. Conflicting event content or
business versions go to quarantine, retaining the original sale. Legacy v1 has
no business replacement protocol. Capture identity is content-addressed; offsets
are contiguous and received clocks monotonic. Reprocessing a record is idempotent;
rewriting an offset or identity fails.

Explicit selected-stream progress defines late events, including exactly at the
frontier. Earlier accepted event time without crossing progress is out of order.
Maximum event time does not create a watermark. Progress advances at the declared
capture position. Every new fact appends an immutable daily revision with received
time, previous revision ID and raw reference. `Replay.aggregates_as_of(cutoff)`
returns copied latest revisions known by cutoff; later arrivals cannot rewrite
earlier queries. Native grain is UTC day/product/**legacy store ID**/channel/
currency. Store ID is not renamed to selling/stock location. Missing grains are
absent, not manufactured zeros.

All aggregates are `partial_selected_sales_fixture`; completeness/model readiness
remain `not_qualified`, transport durability false. The final report is offline
audit metadata, not an as-of fit feature or anomaly score. DLQ rows are offline
fixtures. There are no brokers, ACKs, database projections or live-service changes.

```sh
uv sync --locked --extra snapshot --extra forecast
uv run --locked --extra snapshot retailops-ai-raw-dq build \
  --capture-dir "$OPERATIONAL_CAPTURE_DIR" --curated-dir "$CURATED_DIR" \
  --generated-root data/generated
uv run --locked --extra snapshot retailops-ai-raw-dq verify \
  --replay-dir "$DQ_REPLAY_DIR" --curated-dir "$CURATED_DIR"
make raw-dq-check
```

Publication under `data/generated/dq-replay/<id>` contains both inputs,
`replay.json`, strict manifest and checksum. Identity binds source/snapshot/
qualification/curated parents, input/output hashes, runtime code/contracts, lock
and Python. Verification requires original curated parent and recorded runtime,
recomputes projection/replay and rejects resealed false aggregates. Private staging,
full verification, fsync and no-replace rename protect immutable publication.
Rebuild verifies and reuses existing bytes.

Bounds: 2048 records, 1024 event offsets, 64 KiB UTF-8 body, 128 KiB capture line,
8 MiB capture/output; 12–512 selected sales and 100000 selected parent rows/128 MiB.
Acceptance covers both public families twice in fresh isolated processes with
producer namespaces unavailable, within 300 seconds/1024 MiB each. This proves
sample mechanics/provenance, not full-profile scaling or model quality.

Daily DQ coverage and joins to demand/return inputs remain open. Curated carries
inventory watermarks; sparse return events and sale-day cohorts are not explicit
return-event-day coverage. Cohort maturity concerns original sale day. Return
inputs must retain late availability and post-history tail, and prove coverage
before interpreting missing events as zero. Demand inputs still have
`raw_dq_completeness=not_qualified`. Labels/splits, episode matching, baseline/IF,
held-out multi-seed evaluation, MLflow and serving remain open.

[Data card](../cards/raw-dq-consumer.md), [fixture lineage](../../data/fixtures/raw-dq-v1.md),
[demand inputs](anomaly-inputs.md).
