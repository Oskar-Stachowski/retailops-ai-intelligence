# AI07.4 — native PIT returns and event-day operational views

Scope: [runbook](../../../../reference/return-inputs.md) and
[data card](../../../../cards/return-inputs.md). This slice adds native as-of
return events, purchase cohorts and policies, plus immutable operational return-day
views. AI07 remains open: full DQ/coverage, residual baseline, IF, evaluation and
lifecycle integration remain outstanding. The AI05 working session and services
were not modified.

Runtime: `e2924a8b2143b21cb98e918babcb48cebe947d1d`, based on
`d03f84bb45172ed3ec8e814b4c9b3071d2873863`. The structured
[receipt](verification.json) pins the source, snapshot and current-runtime curated
parents plus all six view identities and content hashes. Final local full-regression
and Required CI results are recorded against the final head in
[draft PR #12](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/12),
which targets the preceding AI07 PR #11.

73 targeted tests passed in 159 seconds, covering native 1.1/1.2 queries, exact
availability boundaries, historical membership, tail events, original routes,
purchase cohorts, rejected claims, exact refunds, cumulative claim limits,
invalid clocks/references, private truth isolation, replaced tables, resealed
false aggregates and blocked self-attested completeness. Ruff/format (370 files),
Mypy (212 files), docs, wire contracts and scoped/working-tree secrets scans passed.
All remaining local make regression gates passed, including previous snapshot,
curated, anomaly, raw DQ and forecast acceptance gates.

Two fresh isolated editable processes completed in **131.986 / 146.555 seconds**,
at **115.172 / 116.953 MiB RSS**. Two detached-wheel processes completed in
**185.761 / 185.796 seconds**, at **114.188 / 114.656 MiB RSS**. All 22 loaded AI
modules came from the detached wheel. IDs, content hashes, counts and publication
bytes matched across all four processes; every source and rebuilt publication
remained immutable. The producer namespaces were unavailable.

| Case | Known July events at 1 August | July events at mature origin | Full-tail events | Events after July |
|---|---:|---:|---:|---:|
| demand | 124 | 130 | 232 | 102 |
| physical | 125 | 130 | 246 | 116 |

The complete query panel has 1,656 rows per case; all retain null observed return
units and `insufficient_data`, including rows with positive known returns.
The full-tail operational summaries contain 272 / 283 refunded units and
23 / 28 rejected units respectively. Purchase cohort maturity remains separate.

The wheel is 687,719 bytes, SHA-256
`3d371a46bba6bdded4a72db5b0608fb088253ec9eded6c2bdbec6ad974196df0`.
All three registered contracts occur exactly once with matching bytes. The API
image built and its isolated resource probe matched those contracts. Detached-wheel
CLI build/verify also passed and reused the exact existing publication.

The new two-process checker is included in `make check`. The checks job budget
increases from 45 to 50 minutes for the additional regressions and measured fresh
acceptance. Each acceptance process retains its **300 s / 1,024 MiB** limit, and
no existing gate is removed or skipped.
