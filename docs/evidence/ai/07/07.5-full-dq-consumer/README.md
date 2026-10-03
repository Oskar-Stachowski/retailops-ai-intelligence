# AI07.5 — independent full operational DQ intake

The [consumer](../../../../reference/full-raw-dq-consumer.md) reconstructs every
canonical sale and native return claim from public snapshot 1.2 and curated
parents. It imports no producer modules and accepts no private fault plan or
labels. Its replay is independently reconciled with the frozen producer output
from [draft PR #83](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/83).

This slice closes independent intake of the complete finite source parent,
including late-tail returns and rejected claims. Per-grain missing business IDs,
quarantine, deduplication, explicit stream progress and immutable as-of revisions
are retained. A clean full capture would establish complete parent fact coverage;
it would still leave business event-day completeness and model readiness
`not_qualified`. No absent grain is converted into a zero observation.

The [fixture lineage](../../../../../data/fixtures/full-raw-dq-v2.lineage.json)
pins both source/snapshot/capture parents. Runtime `1dcf4faf8cee3c628acab7bf61455122129219df` is pinned by the [structured receipt](verification.json).
The 102 targeted tests passed in 346.71 seconds.
Full regression and final-head Required CI results are recorded in the draft PR
after completion; ongoing checks are not treated as successful evidence.

AI07 remains open: reviewed business event-day coverage and DQ qualification
before scoring, seasonal-residual baseline, Isolation Forest, frozen observation
and episode evaluation, and anomaly-specific MLflow/batch/read-only serving
remain required. Broker durability and ACK/crash guarantees belong to AI10.
AI05 processes, databases, releases and worktree are not modified by this slice.

The combined checks budget grows from 65 to 85 minutes: the new 102 cases
took 361.84 seconds in the initial local run, and two additional isolated
processes each retain a 300-second bound. This allows the existing combined
AI05/AI07 gates plus the new work; no test, persistence gate, model limit or
qualification threshold is removed.

Fresh editable processes completed in 227.069 s / 204.49 s.
Fresh installed-wheel processes completed in 228.037 s / 190.171 s.
All four processes reproduced both profiles with identical source/snapshot/curated/
replay identities and published bytes. Each met 300 s / 1024 MiB; producer code
and private fault plans were unavailable. Installed CLI build/verify passed.
Operational receipts, facts, revisions, aggregates, missing IDs and quarantine
match the frozen producer digest, consulted only after replay.
