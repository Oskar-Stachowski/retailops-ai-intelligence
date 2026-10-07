# AI07 — point-in-time return event-day views

`retailops-ai-return-inputs` publishes an immutable operational view of returns
known at an explicit UTC origin. It consumes only verified curated 1.2 tables:
`return_events`, `inventory_sales`, `sale_price_references`, `product_catalog`,
and `return_policies`. It loads no producer code or private injection plan.
The bounded query covers 1–366 inclusive calendar days, at most 100,000 input
rows / 128 MiB and 100,000 output rows / 128 MiB. Each output record is at most
64 KiB. Publication uses private staging, parent replay, fsync and no-replace;
an identical rebuild verifies and reuses the existing directory.

```sh
uv run --locked --extra snapshot retailops-ai-return-inputs build \
  --curated-dir data/generated/curated/<curated-id> \
  --start-date 2026-07-02 --end-date 2026-09-08 \
  --as-of-time 2026-09-09T00:00:00Z \
  --generated-root data/generated

uv run --locked --extra snapshot retailops-ai-return-inputs verify \
  --input-dir data/generated/return-inputs/<return-input-id> \
  --curated-dir data/generated/curated/<curated-id>

make return-inputs-check
```

`returns.jsonl`, `return_manifest.json` and `manifest.sha256` are the exact
publication allowlist. Extra truth files are rejected. The descriptor binds
the curated/source/snapshot/qualification IDs, query policy, content digest,
runtime code, registered schemas, dependency lock and Python version. Verification
recomputes every point from the immutable operational parent, including after
an attacker reseals a changed quantity and all public hashes.

## Return day and purchase day

The output grain is **return business date / product / original selling location /
channel / currency**. A return on 2 August for a 31 July purchase belongs to
2 August here. Its purchase remains in the 31 July sales cohort. Returns after
the source sales history or product discontinuation retain the original purchased
line, paid price and physical stock route. They do not extend the active sales panel.

Visible records must have `returned_at` and causal `curated_available_at` at or
before the origin. The projection checks concrete purchase/order/item, category /
channel policy, original route, known purchase and policy, return window, ingestion
bound, cumulative claims and exact decimal refunds. Refunded quantities and money
are summed separately from rejected claims. Rejected quantities consume the
claim limit, but do not reduce sales units or revenue.

The dense query panel uses series with a purchase known at the query origin.
Its scope is **purchases in the parent source only**. It makes no claim about
pre-history purchases, carry-in returns or live ingest coverage. It is a view
at one common origin, not a rolling training feature dataset.

## Unknown coverage stays unknown

`known_refunded_units`, `known_rejected_units`, `known_refund_amount` and
`known_event_count` summarize visible members. A zero in those fields means no
matching **known** fact, not a complete zero-return business observation.
Every point retains `observed_return_units=null`, `coverage_status=not_qualified`,
`status=insufficient_data`, and `return_event_day_coverage_unavailable`.
The model feature allowlist is empty and detector readiness is `not_qualified`.

The source's mature `daily_return_cohorts.return_data_complete` flag describes
a **purchase-day cohort** at its explicit cutoff. It cannot qualify an event-day
stream. Neither the latest observed event nor selected-sales DQ progress proves
return coverage. Positive known returns also remain partial until that gate is met.
No baseline, IF, score, alert, promotion, service or broker durability is claimed.

Later availability produces a separate immutable identity, leaving the earlier
view unchanged. The point carries a digest of member IDs, operational source
record hashes and availability clocks, plus the latest member availability and
original stock IDs. Unavailable future members do not affect those values.

## Native curated queries

`CuratedReader.rows(origin, table=...)` supports returns on curated 1.1 and 1.2:

| Table | Selection | Optional `business_date` |
|---|---|---|
| `return_events` | All immutable events occurred and available by origin | Return-event UTC day |
| `daily_return_cohorts` | Latest known `as_of_time` per purchase-day native grain | Purchase UTC day |
| `return_policies` | Policies known and available by origin | No date filter |

Historical queries never select a future tail cohort. Every query verifies the
selected table's original digest before yielding rows. Ambiguous latest cohort
cutoffs or a replaced table fail closed.

## Acceptance and next gate

The frozen public demand/physical source fixture is
`anomaly-v1_2.zip` with SHA-256
`c344a5cfeea086e1054add724c830d747815e9eab425a19a17c1d0691671066c`.
The independent checker runs two fresh isolated processes for both cases,
builds/rebuilds/verifies early, corrected and full-tail views, compares native
as-of membership, and verifies immutable source bytes. Each process is limited
to 300 seconds and 1,024 MiB RSS. Installed-wheel acceptance uses the same worker.
Measured results belong in [evidence](../evidence/ai/07/07.4-return-inputs/README.md)
and the [data card](../cards/return-inputs.md).

The next source change must publish versioned operational event-day coverage,
source-history scope, availability bounds and DQ qualification for the full sales
and return stream. That enables complete zeros and scoreable daily observations.
Residual baselines, IF, labels/splits, episode evaluation and AI05 lifecycle
integration remain subsequent AI07 work.
