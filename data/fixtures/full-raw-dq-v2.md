# Public full-DQ v2 fixture

This explicitly reviewed synthetic tiny-fixture exception contains two public
snapshot 1.2 exports and exactly two operational capture files per profile:
`{demand,physical}/public/**` and
`{demand,physical}/capture/{raw/events.jsonl,source_binding.json}`.
It contains no simulation truth tables, labels, fault plans or producer replay.
Source metadata retains content hashes for lineage; those hashes are not labels.

Producer code is draft PR #83 at `1fc2787d21a49911fcf663cfd67b4407f3a85986`;
accepted generation runtime is `ae1178ccfc7f3019645a364acb4ca210f8a05c95`.
The source parents and capture bytes come from its frozen four-process acceptance.
Public snapshots were independently exported and imported after that acceptance.
[Lineage](full-raw-dq-v2.lineage.json) pins every member path, size, SHA-256,
source/snapshot/producer-fixture identity and evaluation-only operational digest.

ZIP SHA-256: `e899afaeb045a30d62b47fe2f8c9dfb6449ae2fc8f6959750c08e254c7c99775`.
Compressed size: 2,104,589 bytes; expanded size: 6,146,340 bytes.
The archive has deterministic member timestamps and permissions.
Acceptance checks exact inventory and checksums before bounded extraction.

Demand covers 1,401 sales and 232 native return claims; physical covers 1,390
sales and 246 claims. Both captures deliberately leave six canonical facts
missing after quarantine. Each has two event duplicates, two business duplicates,
two late arrivals and two out-of-order arrivals. Missing-context records preserve
their unchanged operational facts. The last native return occurs on 31 August,
with source availability on 2 September, beyond the July sales window.
The fixture qualifies finite-parent replay only, never complete business days.

Use `make full-raw-dq-check` or the
[consumer runbook](../../docs/reference/full-raw-dq-consumer.md).
