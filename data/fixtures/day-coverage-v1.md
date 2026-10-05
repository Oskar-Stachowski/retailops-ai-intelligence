# Scoped business event-day coverage fixture

This reviewed, bounded archive contains only the additive three-file day-closure
artifacts for demand and physical source 2.8 profiles. It accompanies
[full raw-DQ v2](full-raw-dq-v2.md), whose public snapshots and capture remain unchanged.
There is no private plan, simulation table, fault truth or evaluation label in the
coverage archive. Exact sizes, hashes and parent IDs are in
`day-coverage-v1.lineage.json`.

The producer independently recreated both native sources with their original
source IDs and generated the declarations through `data.day_coverage.package`.
The archive has 1713 demand rows and 1717 physical rows, including explicit scoped
zero-return days and the return-window tail beyond the July sales interval.

Consumer acceptance imports the independent public parents, verifies the full raw
replay, reconstructs all declarations from public native facts, and applies the
delivery-time gate. Publisher declarations cannot substitute for accepted raw facts.
The return scope is `purchases_in_parent_source_only`; cohort maturity and transport
progress are not day closure. The source, snapshot and legacy transport schemas
and fingerprints remain unchanged.
