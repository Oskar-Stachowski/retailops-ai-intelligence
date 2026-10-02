# AI07 — independent selected-sales DQ acceptance

Frozen runtime: `89d03e6ae9b54406d9ea0c6b579b60f6c220ce81`.
[Verification receipt](verification.json) binds source/raw fixture hashes,
parent/artifact identities, replay hashes, packaged contracts and measured runs.

36 targeted tests passed (90.84 s), including conflict/duplicate identity,
explicit progress versus maximum event time, exact clock boundaries, late as-of
revisions, missing grain versus zero, invalid JSON/money/versions/routes,
forbidden legacy optional fields, source binding, truth-file rejection and
resealed false aggregates. Ruff/format (358 files), Mypy (206 files), documentation,
wire contracts and secrets checks passed. Full-suite and Required CI results
are recorded separately in the PR before final acceptance; this receipt does
not substitute targeted tests for those checks.

Both public scenario families ran twice in fresh editable processes and twice
from a detached installed wheel. Producer `data` and `services` namespaces were
unavailable; every loaded AI module in wheel runs came from the installed wheel.
All four runs yielded identical parent IDs, replay IDs and bytes. Elapsed time
was 56.324–69.269 s, peak RSS 101.844–111.781 MiB, below 300 s/1024 MiB limits.
Snapshot, capture and published bytes remained unchanged after rebuild/full verify.

Each case has 256 selected canonical sales, 258 deliveries and 29 progress
records, yielding 253 accepted facts/revisions, one exact duplicate, one business
duplicate, three quarantine/offline-DLQ records, one late and one out-of-order
fact. Every operational section matches the accepted producer replay. Only
operational raw/binding files enter the consumer; the parity oracle is test-only.

Wheel SHA-256:
`72bb72e2bc401b28a2836c43fb24589eb0ff5b59440bbeac62b5dabb20f2cf0a`.
The wheel contains the five reviewed DQ contracts once, matching registry bytes.
The API Docker image built with the new registry and an ephemeral, read-only,
network-disabled probe read all five resources. No ports, volumes or running
service stack were involved.

This accepts the independent selected-sales DQ boundary. It does not qualify
full daily coverage, return-event-day completeness, model quality, broker
durability or AI07 as a whole. [Runbook](../../../../reference/raw-dq-consumer.md)
and [card](../../../../cards/raw-dq-consumer.md) retain those limits.
