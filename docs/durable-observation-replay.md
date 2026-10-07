# Durable AI observation receiver — bounded AI10 increment

This extends the [in-memory receiver](source-observation-replay.md) with a real
PostgreSQL projection. It does **not** turn live Source REST into an immutable
snapshot, create a Source emitter/topic, or qualify AI07/AI08. Source owns a
separate operational publisher/capture for `daily_demand_versions`; its
[same-broker Source to AI SQL/ACK receipt](evidence/ai10-source-sql-handoff-accepted.json)
has passed. Full 43-table immutable bundles remain separate from this stream;
general 43-table SQL snapshot/handoff is explicitly unsupported. The
[authenticated broker adapter](observation-broker.md) builds on this SQL core.

## Processing and recovery

1. Apply explicit Alembic migration `0021_observation_replay`. No API startup
   migration or new dependency is introduced. Six new `ai.observation_*` tables
   are additive; downgrade removes this projection, leaving prior AI tables.
2. Supply a trusted `Stream` and partition count from the authenticated transport
   adapter. `ObservationStore.claim()` pins authority, cluster, native topic ID
   and complete partition count per group. Every group starts from offset zero;
   a retention gap, log rewind or broker commit ahead of SQL requires resync.
3. Claiming a partition increments its epoch and changes its owner. Processing
   and release check that fence. A group row lock serializes writes across
   partitions so competing fact/event/grain identities cannot race.
4. Submit a bounded `TransportRecord`. Preserve value bytes, key, ordered
   duplicate headers and timestamp. A transport fingerprint detects changed
   bytes or metadata at an old offset; semantic hashes bind accepted envelopes
   and native fact versions. A transport gap stops before mutation.
5. In **one transaction**, validate or quarantine, append immutable fact versions
   when valid, retain the exact raw receipt, and advance the checkpoint. Invalid
   JSON, duplicate JSON keys, nonfinite values and semantic collisions produce
   fixed quarantine reasons. A foreign authority stops processing instead of
   advancing. Oversized transport and bounded-store exhaustion also stop.
6. Call `process_then_ack()` with the transport adapter's acknowledgement
   function. It invokes the function only after the SQL transaction has returned
   from commit. A failed ACK or SIGKILL after commit leaves a durable receipt;
   matching redelivery has no second business effect. Database failure prevents
   the callback. This module does not authenticate or contact a broker itself.

Corrections replace the eligible version's contribution at an as-of origin;
they do not add old and new quantities. Out-of-order versions are retained, but
incomplete history cannot be read through a capture. Natural grains, row IDs,
version payloads and event IDs cannot silently change. Groups have independent
fact histories; one group's later progress cannot contaminate another capture.

## Consistent capture of the AI projection

1. Call `capture(group, stream=trusted_stream, partitions=trusted_count)`.
   PostgreSQL `REPEATABLE READ` reads the partition vector, raw receipts, version
   rows and counters at the same SQL boundary.
2. Reconstruct the existing strict single-table wire capture from all receipts.
   Every position from zero must be represented and bound to an exact fact.
   Reject any quarantined prefix, missing version, inconsistent stored fact,
   incomplete vector, changed identity or size/count overflow.
3. Persist the canonical capture bytes and SHA-256 in that transaction. The
   final stream lock prevents publishing across a concurrent write: PostgreSQL
   rejects the stale snapshot with a serialization failure. Retry the entire
   capture explicitly; there is no automatic skip or partial publication.
4. Read an already sealed capture with `load_capture()`, supplying the same
   trusted stream and partition count. Verify its stored byte hash and all
   original wire semantics before replaying any overlapping records.

Limits per group: 10,000 fact versions, 20,000 transport receipts, 32 partitions,
32 saved captures and 16 MiB per capture. Raw values are limited to 32 KiB, keys
to 4 KiB, and ordered headers to 16 entries / 4 KiB. This is a bounded drill and
integration primitive, not an unbounded production retention policy. Access is
an internal Python/SQL interface using the AI database credentials; no public
HTTP endpoint or private fact logging is added.

## Mandatory evidence

Run `make bootstrap observation-persistence-test` on an owned Docker runner.
`Required CI` runs it in a separate mandatory `observation-replay` job so it
does not extend the existing long persistence job. The aggregate gate requires
this job in addition to all previous gates. Local Docker is not started.

The drill uses an immutable PostgreSQL 16 image and cleans up only its own
disposable container. It checks actual transactions, raw quarantine, concurrent
cross-partition dedup, epoch fencing, gaps, authority/topic replacement, SQL
failure before ACK, SIGKILL before commit and after commit before ACK, coherent
capture under concurrent writes, tampered captures, additive migration rollback,
and native fixture capture plus overlapping replay versus full replay.
It also verifies `pg_dump` / `pg_restore` of all six nonempty projection tables,
including raw quarantine bytes and a still-readable sealed capture.

The JSON receipt identifies actual SQL storage and explicitly records
`source_live_capture_supported=false`, `broker_ack_performed=false` and
`full_43_table_handoff=false`. The JUnit artifact records executed PostgreSQL
checks. Broad-suite skips of these Docker-only checks are covered by this
mandatory job and must not be counted twice.
