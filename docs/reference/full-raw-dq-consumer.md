# Full operational DQ intake (AI07)

`retailops-ai-full-raw-dq` implements `ai-full-parent-replay-2.0.0` independently
of RetailOps producer code. The separate capture/binding v2 covers every sale
and native return claim in one verified source parent. The selected-sales v1
CLI, registry and frozen fixtures retain their original scope.

The capture directory contains exactly `raw/events.jsonl` and
`source_binding.json`. Private fault plans, labels, producer aggregates and extra
files are rejected. Snapshot 1.2 import and curated parents are required.
The consumer verifies their bytes, typed content, qualification and source IDs,
compares all six bound source table identities and reconstructs curated data
from the public import. It independently regenerates the complete operational
projection and its SHA-256. A selected list or a resealed false parent cannot
stand in for the full source.

```sh
retailops-ai-full-raw-dq build \
  --capture-dir /absolute/path/capture \
  --import-dir /absolute/path/data/generated/snapshots/source-sha256-... \
  --curated-dir /absolute/path/data/generated/curated/curated-sha256-... \
  --generated-root /absolute/path/consumer/data/generated

retailops-ai-full-raw-dq verify \
  --replay-dir /absolute/path/consumer/data/generated/full-dq-replay/full-dq-replay-sha256-... \
  --import-dir /absolute/path/data/generated/snapshots/source-sha256-... \
  --curated-dir /absolute/path/data/generated/curated/curated-sha256-...

make full-raw-dq-check
```

The existing OPS07 wire remains schema 1.0 and topic `retailops.sales.v1`.
Its legacy order store is checked exactly. Published facts use the original
native selling location, stock location, channel and currency. Return status
comes from the verified native parent; it is never inferred from a zero refund.
Every return claim contributes claim units. Only refunded claims contribute
refunded units and money; rejected claims contribute rejected units.
No purchase before the source parent is invented. Tail returns after the last
sales day remain in the projection, using native return availability.

Malformed/nonfinite JSON and unsupported wire versions enter offline quarantine.
Event IDs cannot hijack another parent fact. Exact duplicate events and
alternative IDs for the same unchanged business fact are deduplicated. Only
sales SKU and return order ID may be absent as redundant context. Any other
field, money, quantity, route, source or clock change is rejected. Contiguous
capture offsets, monotonic receipt times and advancing explicit progress are
mandatory. Native facts cannot arrive before their availability.

Each accepted arrival creates an immutable aggregate revision at `received_at`.
`Replay.aggregates_as_of(cutoff)` returns only revisions known by that instant;
late deliveries cannot rewrite old views. `parent_fact_coverage` lists expected,
accepted and missing business IDs per event type/day/native selling grain.
Only grains actually present in the source parent are included. An absent grain
is unknown; the consumer does not generate dense zero observations.

**Complete parent coverage does not close a business event-day.** Progress means
an explicit frontier of this finite parent stream. Every aggregate revision and
coverage row retains `business_event_day_completeness=not_qualified`; the manifest
retains unqualified curated completeness and model readiness. Purchase-cohort
maturity is also insufficient. A separate reviewed event-day coverage policy
must precede qualified demand/return features and anomaly scoring.

Limits are 4,096 canonical parent events, 8,192 delivery offsets, 16,384 total
records, 64 KiB per body and 32 MiB per capture/replay. Native parent loading is
bounded by 100,000 rows and 128 MiB. Metadata, schemas, dependencies, Python and
all used runtime modules bind the immutable artifact ID. Publication uses
staging, semantic replay verification, fsync and no-replace rename; retry verifies
and reuses identical bytes. A checksum alone does not qualify a modified replay.

The [public fixture](../../data/fixtures/full-raw-dq-v2.md) contains both 30-day
ai-smoke parents and operational captures, with no private fault plan or labels.
The acceptance script runs both profiles twice in isolated processes and compares
all published identities and bytes with producer namespaces absent. Producer
operational hashes are consulted only after consumer replay, as evaluation evidence.
The [versioned receipt](../evidence/ai/07/07.5-full-dq-consumer/README.md) records
measured editable/wheel results. No broker durability, ACK, crash recovery,
model quality, anomaly lifecycle or stage-wide readiness is claimed here.
