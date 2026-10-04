# AI 08 — later calibration, operator proposal and portable runtime

This increment preserves the accepted 1.0 qualification and all earlier parent
identities. The separate 2.0 selection protocol fits the base on original TRAIN
(known at 5 June), a regularized conditional sigmoid on original TUNE (known at
24 June), and selects on original CALIBRATION (known at 13 July). All cutoffs
require strict label availability and purging. The calibrator uses the raw score
and categories, physical stock locations and historical constraint flags known
at each origin. Its complete vocabulary and coefficients are portable.

[The frozen policy](stockout-later-selection-policy.json) compares the existing
six base variants with four regularization values. Selection minimizes failed
segments, then Brier, then maximizes AP, with deterministic tie breaks. Segment
support and ECE requirements remain unchanged. These are development selection
metrics, **not an independent quality assessment**. Only seed 42 may fit/select.
Final outcomes require a separate frozen campaign.

`stockout_selection` replays all actual parent bundles, verifies exact eligible
role coverage, seals parents before/after, binds the real producer seed and
creates a card 3.0 with genuine selection/model/calibrator identities. Exported
recipes reproduce sklearn predictions without pickle or executable model data.
Unknown future category/location values get zero offsets; they never refit the
vocabulary. Old sigmoid recipes remain versioned separately.

`stockout_policy` evaluates explicit thresholds, one capacity and false-attention
versus missed-incident costs. Capacity applies once per physical origin, before
segment summaries. [The development proposal](stockout-operator-development-proposal.json)
uses 0.25/0.5/0.9 bands, top 20%, and hypothetical 1/5 cost units. It remains
unapproved and does not establish business savings or permission to publish.
The proposal binds the exact selection, model and calibrator.

`stockout_runtime` scores public PIT features and actual physical upstream
forecasts. Public input assembly has bounded scope and independently replays
public parents; it has no private-label argument. Scored, already-stockout,
insufficient-data and stale-input outcomes are distinct. Non-scored outcomes
have no fabricated probability or band. Factual reasons are not causal claims.
Output freshness and inventory freshness are separate; absent curated
watermarks remain unknown. Stockout authorization is explicitly separate from
forecast capabilities and checks physical scope.

## Isolated preparation

[The workflow](../../.github/workflows/ai08-cohort-preparation.yml) freezes source
`08639e9`, preparation consumer `4faaf4b`, and the current control commit.
Three matching 30-product, 2-physical-location, 102-day cohorts use seeds
42/137/2026. At most two isolated GitHub jobs run together. Only seed 42 builds
the new development model and compares native/installed-wheel bytes, then
replays its operator proposal. Other seeds prepare unopened holdout parents.

Profiles 1.6 prospectively allow 1280 MiB RSS and 576 MiB scratch after the
measured 30-product baseline. Profiles 1.7 have the same geometry and limits,
but are restricted to isolated Linux GitHub runners with a 6 GiB ephemeral-disk
reserve. The local computer retains its **50 GiB** policy; remote profiles cannot
run locally. Input/qualification/row caps and quality requirements are unchanged.
The baseline and earlier failed receipts remain separate, immutable evidence.

Only seven verified consumer-parent trees enter a bounded, lossless checkpoint;
code, virtual environments, credentials and duplicate raw exports are excluded.
Every file has its SHA, size and mode verified from the archive and its source.
A source checkpoint survives a later model-proof failure. Such a checkpoint
means preparation passed, never quality, approval, promotion or AI 08 ready.
Workflow artifacts expire after seven days and need verified archival before
then. No registry or serving credentials are provided to preparation jobs.

## Remaining acceptance

Full real-parent acceptance of 2.0, independent approved final robustness across
three seeds and four meaningful scenarios, reviewed operational policy, typed
AI 05 lifecycle/MLflow integration, durable database batch, HTTP reads, recovery,
container/resource proof, required CI, review and merge remain mandatory.
Unit-tested portable scoring alone cannot authorize production publication.
