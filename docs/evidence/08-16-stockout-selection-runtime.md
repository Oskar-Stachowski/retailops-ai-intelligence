# AI 08.16 — development selection and portable scoring foundation

This is an implementation increment; **AI 08 remains not ready**.
[The reference](../reference/stockout-selection-runtime.md) defines the new
2.0 roles, operator proposal, portable runtime and isolated preparation.
No final outcome, threshold approval, model promotion or lifecycle publication
is claimed here. Earlier 1.0 artifacts and their failed quality gates remain
unchanged.

The previously accepted complete cold 30-product pipeline has 6120 origins,
1305 TRAIN, 493 TUNE and 466 CALIBRATION rows; 477 final-test members have no
evaluated outcome vector. It took 1535.14 s, peaked at 852.08 MiB RSS and
465.35 MiB allocated scratch. Its original 1.0 qualification still fails three
category ECE gates. A separate internal cached-input diagnostic of later
conditional calibration selected LR with upstream, C=10: selection Brier
0.063029, AP 0.980947, ECE 0.044082. These numbers motivated the explicit new
development protocol; they are **not accepted real-parent replay or independent
quality**. A warm 19-product diagnostic still fails Books ECE and is not pooled
with the cold 30-product cohort.

Earlier warm seed-137 preparation correctly stopped above its old 1 GiB RSS
limit, at 1,085,685,760 B. New matching profiles prospectively raise only resource
headroom before another attempt. No completed cohort or quality result is
invented for this failed attempt. Local native policy acceptance also stopped
when the 50 GiB disk reserve was crossed. Failed receipts remain retained.

Focused regression covers chronological separation, mutation of selection
labels/features without refitting base/calibrator, omitted eligible rows,
strict seed and policy identities, complete native/portable probabilities,
physical capacity, unknown vocabulary, public-input replay and mutation,
freshness/unsupported outcomes, separate authorization, resource-profile
boundaries and verified checkpoint archives. Static Ruff/format and strict
mypy pass. The combined focused run has **235 passed, 0 warnings**, in 55.41 s:
141 new cases and 94 existing authorization/resource cases. Mypy checks
408 source files; format checks 527 Python files. Access snapshots and
documentation links also pass. [The receipt](08-16-stockout-selection-runtime.json)
keeps implementation acceptance separate from unopened quality/production gates.
Current full CI and actual remote preparation still require their
own receipts; successful earlier HEAD CI does not substitute for this code.

The next work is actual 2.0 native/wheel acceptance, then typed lifecycle and
durable batch/read integration while matching cohorts finish. A concrete final
campaign and operational policy must be reviewable before their approval.
