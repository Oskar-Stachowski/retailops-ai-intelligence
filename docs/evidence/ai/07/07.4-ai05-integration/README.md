# AI07.4 — compatibility with the accepted AI05 foundation

Runtime `146e2d1bb9223ec3b5745b9cb433674d33c63073` merges AI05 `main`
`f4ae14fe6588b91506383a709b5a0185208a4ea6` into the AI07 inputs stack ending
at `90d54633dbb46956d21e71ad1c227f056a83af61`. The integration is reviewable in
[draft PR #13](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/13).
That PR includes the preceding AI07 drafts #9–#12 against the current main.
This receipt qualifies compatibility of the shared import/curated/runtime foundation;
detector registration and promotion still require their own anomaly contracts.

The version-aware importer retains AI05's reviewed ordinary and forecast schema
pairs for snapshot 1.1, and AI07's exact anomaly schema set for snapshot 1.2.
Resealed mixed pairs and substitution of forecast schemas into an anomaly snapshot
are rejected. Curated data carries the independently verified sales watermark.
Its meaning remains `synthetic_sales_day_close_without_return_guarantee`;
it cannot qualify a selected raw fixture or a return event-day. The API image
includes all AI07 contracts and AI05's forecast/snapshot dependencies. Required CI
retains the forecast runtime gate and all persistence/recovery gates alongside
the anomaly, raw DQ and return acceptance gates.

The combined suite has 1,849 tests, versus AI05's 1,730 and the earlier AI07
stack's 1,303. The accepted AI05 full pytest alone took 2,261.81 seconds in CI;
AI07 also adds six fresh artifact processes. The checks job budget is therefore
65 minutes. Every test and gate remains required; each AI07 acceptance process
retains its 300-second / 1,024-MiB bound. This adjusts the total job budget,
not model execution limits or qualification thresholds.

73 targeted tests passed in 53.25 seconds, including six new schema-boundary
regressions. Ruff/format checked 573 files; Mypy checked 338 sources. Documentation,
all HTTP/runtime contracts and scoped/working-tree secret scans passed.
Full regression and final-head Required CI receipts are recorded in the PR,
so this runtime-pinned evidence does not claim results for a later untested head.

The [structured read-only receipt](verification.json) checks the frozen fresh
AI05 source through this integrated runtime with the producer namespace absent:
43 public tables and 27,587 rows, full import and curated verification, preserved
sales completeness through 1 October, and immutable retry. It completed in
76.779 seconds. Source bytes and all published bytes were unchanged on retry.
This is an input compatibility check, not a repeat of the mutating AI05 serving
demonstration. The AI05 checkout, databases, services and releases were not changed.

Changing shared transformation code changes current-runtime curated and derived
IDs. Rebuild downstream artifacts against their new explicit parents; retain
historical frozen receipts and require a new model qualification where applicable.
An old forecast approval cannot be copied onto an anomaly dataset or detector.

AI07 remains open. Next are versioned full sales/return replay and event-day
coverage, DQ qualification before scoring, seasonal residual baseline, Isolation
Forest, frozen observation/episode evaluation and anomaly-specific lifecycle.
The historical return views continue to preserve unknown completeness and null
observed quantities; purchase-cohort maturity does not establish return-day coverage.
