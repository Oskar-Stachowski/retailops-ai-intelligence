# AI07.5 — independent full operational DQ intake

Runtime `bd1f2259a48149453f58729c80b11a69d51e1dfb` is pinned by the [structured receipt](verification.json).
The [consumer](../../../../reference/full-raw-dq-consumer.md) reconstructs every
canonical sale and native return claim from public snapshot 1.2 and its verified
curated parent. Producer modules, private fault plans and labels are excluded.
Evaluation compares operational output with the frozen producer from
[draft PR #83](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/83)
only after independent replay.

All 106 targeted tests passed in 257.96 seconds. They include both consumers'
canonical JSON regression (`False` cannot borrow the sealed integer partition 0)
and corrupting a staged aggregate before publication in each profile.

Fresh editable processes completed in 134.715 s / 143.743 s.
Fresh installed-wheel processes completed in 129.601 s / 168.123 s.
Peak RSS across these four processes was 155.516 MiB. Each process reproduced
both profiles, their source/snapshot/curated/replay identities and immutable DQ
publication bytes within the unchanged 300 s / 1024 MiB limits. Installed CLI
build/verify and the installed-wheel legacy v1 acceptance also passed.
Receipts, facts, revisions, aggregates, missing IDs and quarantine match the
frozen producer digest; operational replay hashes are unchanged by this fix.

The prior head passed 1953 tests and persistence CI, but failed the full-DQ
resource gate. Its local full regression similarly timed out in this worker.
Historical measurements remain explicit in the receipt and do not qualify the
changed runtime. The builder now reconstructs the parent once, checks staged
bytes against that trusted material, and verifies existing destinations before
reuse. Public verification still reconstructs the full parent and replay.

The [fixture lineage](../../../../../data/fixtures/full-raw-dq-v2.lineage.json)
pins both source/snapshot/capture parents. Full regression and final-head Required
CI results are recorded in the draft PR after completion. The checks budget is
85 minutes; no persistence gate or acceptance limit has been removed.

Complete finite-parent coverage still leaves business event-day completeness
and model readiness `not_qualified`. Missing grains remain unknown, not zero.
AI07 remains open: reviewed event-day coverage and DQ qualification before
scoring, seasonal-residual baseline, Isolation Forest, frozen observation/episode
evaluation and anomaly-specific MLflow/batch/read-only serving remain required.
Broker durability and ACK/crash guarantees belong to AI10. This slice does not
modify AI05 worktrees, processes, databases or releases.
