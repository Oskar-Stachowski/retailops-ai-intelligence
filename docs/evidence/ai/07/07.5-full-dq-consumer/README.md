# AI07.5 — independent full operational DQ intake

Runtime `b85ddcd2a8edc611c65154e4249b2cb0d69769d3` is pinned by the [structured receipt](verification.json).
The [consumer](../../../../reference/full-raw-dq-consumer.md) reconstructs every
canonical sale and native return claim from public snapshot 1.2 and its verified
curated parent. It imports no producer modules and accepts no private fault plan
or labels. Evaluation compares its operational output with the frozen producer
from [draft PR #83](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/83)
only after independent replay.

All 104 targeted tests passed in 406.22 seconds.
Final review also required canonical JSON byte comparison: Python considers
`False == 0`, but a boolean transport partition cannot borrow the record ID of
integer partition 0. Both offline consumers reject it; two explicit regressions
cover v1 and v2. Valid captures retain their operational behavior. Earlier
measurements are historical and do not qualify a changed runtime.

Fresh editable processes completed in 218.56 s / 235.025 s.
Fresh installed-wheel processes completed in 287.119 s / 255.992 s.
All four processes reproduced both profiles, their source/snapshot/curated/replay
identities and immutable DQ publication bytes. Each met 300 s / 1024 MiB;
producer namespaces and private fault plans were unavailable. Installed CLI
build/verify passed. Receipts, facts, revisions, aggregates, missing IDs and
quarantine match the frozen producer digest.

The [fixture lineage](../../../../../data/fixtures/full-raw-dq-v2.lineage.json)
pins both source/snapshot/capture parents. Full regression and final-head Required
CI results are recorded in the draft PR after completion. The checks budget is
85 minutes, allowing the existing combined AI05/AI07 gates, 104 added cases and
two additional processes with their unchanged 300-second bound. No persistence
gate, model execution limit or qualification threshold is removed.

This slice verifies the complete finite source parent, including late-tail returns
and rejected claims. Per-grain missing business IDs, quarantine, deduplication,
explicit stream progress and immutable as-of revisions remain explicit.
Complete parent coverage still leaves business event-day completeness and model
readiness `not_qualified`. No absent grain becomes a zero observation.

AI07 remains open: reviewed event-day coverage and DQ qualification before
scoring, seasonal-residual baseline, Isolation Forest, frozen observation/episode
evaluation and anomaly-specific MLflow/batch/read-only serving remain required.
Broker durability and ACK/crash guarantees belong to AI10. The AI05 worktree,
processes, databases and releases are not modified by this slice.
