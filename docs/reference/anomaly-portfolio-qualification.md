# AI 07 portfolio qualification

The frozen v4 experiment passes all 56 original quality/sample checks for both
saved model families, and the complete local OCI/PostgreSQL 16/MLflow acceptance
passes. AI 07 awaits Required CI on the final source and consumer revisions;
its stage status is `pending_required_ci`. See the
[completion receipt](../evidence/07-completion.md).

The opened v2 final test failed the original
frozen recall, episode recall and evaluable coverage gates. Neither frozen
Isolation Forest model may be promoted. The exact failed thresholds, selections
and final-report hashes are retained in
[evidence](../evidence/07-v2-final-not-ready.json).

The v3 count-rate baseline versions also failed final precision (0.7671/0.7698
against 0.80) and return false alerts (13.89 per 1000 against 10). Their saved
models, original selections and full failed quality results remain in
[v3 evidence](../evidence/07-v3-final-not-ready.json). Neither is promotable.

The v4 experiment was separately [declared](../evidence/07-v4-experiment-declaration.json)
on `ai-07-portfolio-v4`, 2025-01-01 through 2025-05-08. It retains v3's
128 days, 12 products, store/online geography, two stock locations, uniform
0.50 demand factor, supply policy, complete 8192-event budget, seeds, five
intervention types and magnitudes, conservative unknown spillover mask and all
original numeric quality/sample gates. Nine explicit event-capacity and severity
configurations use the existing count-rate multiscale recipe; both real saved
model families were compared using seed-42 validation only. All six final
windows were scored after selection and complete public lineage were frozen.
Repeated synthetic experiments are correlated. The qualification covers
only the declared v4 scope, not opened v2/v3 cohorts or production.

The [v4 saved validation comparison](../evidence/07-v4-validation-summary.json)
selects the seasonal-residual baseline with sale/return capacities 0.10/0.005
and high capacities 0.05/0.0025, and the genuine IF reference with capacities
0.075/0.005 and high capacities 0.025/0.001. Both achieved validation precision
1.0 and episode recall 1.0, with observation recall 0.925 and 0.775 respectively.
These are validation results. Validation has one
development seed and one physical episode per type; all original six-case
inventory and three-episode-per-type gates remained mandatory in the final test.

The [frozen final receipt](../evidence/07-v4-final-qualified.json) records
primary precision 0.964286, recall 0.90 and episode recall 32/33; the native
Isolation Forest reference records precision 0.956044, recall 0.725 and episode
recall 33/33. Both have 0.962232 false alerts per 1000 clean observations,
high-severity precision 1.0 and evaluable coverage 0.578598 (4277/7392).
Unknown truth (2232) and insufficient inputs (883) remain explicitly excluded.
All type, segment, sample and six-case checks passed. The failed v2/v3 finals
remain unchanged and do not gain qualification from v4.

## Explicit scoring recipe

The original model and numeric rows remain supported without changing their
identity or predictions. `anomaly-portfolio-model-2.0.0` explicitly declares
`causal-count-residuals-1.0.0`; fit policy 2.0 allows three additional numerical
features. Current daily input must still be `ready_input`. The added features
use only prior ready outcomes from the same business series whose scoring
origins are at or before the current origin. Labels, generator seed, injection
magnitude and future outcomes are excluded.

Three-day and seven-day count residuals sum observed minus seasonal expected
counts across known outcomes, including the current outcome. Their scale is
`max(1, sqrt(sum(expected)))`, a declared count-variance rule. At least two
known outcomes within three calendar days and four within seven are required;
missing outcomes remain absent, never zero. The saved numeric row carries
known lag counts and independently validates its derived residuals. The
inventory feature is `8 * max(expected - observed, 0) / expected` only when
expected is positive and known on-hand is at most one; otherwise it is zero.
Unknown current input still receives no score.

The explicit seasonal-residual baseline takes the largest absolute daily
robust residual, available multiday count residual or inventory shortfall
score. The coefficient and formula belong to the frozen versioned recipe.
This contextual rule is never substituted for Isolation Forest. IF uses its
actual saved native fitted forest and declared features. Both families are
compared on seed-42 validation; final data cannot select features or thresholds.

The initial nine v3 fits did not satisfy the original validation gates. Their
artifacts and comparison remain unchanged. The subsequent development
[amendment](../evidence/07-v3-development-amendment.json) declares
`anomaly-portfolio-model-3.0.0` and `causal-count-residuals-2.0.0` before final
scoring. Multiday expected counts now use the mean of qualified observations
in the current point's prior 28-day history, all known at its fit cutoff,
multiplied by the number of known window outcomes. Current/future outcomes,
labels and unknown days cannot enter this mean. Daily seasonal residuals remain
unchanged. The stock rule applies only to sales and uses that prior rate.
The model explicitly records distinct sale/return alert and high-severity
capacities. Thresholds still use all eligible unlabelled demand validation
scores, including observations whose offline truth is unknown. Validation
labels compare the saved configurations; they cannot filter fitting rows.

The saved nine-fit comparison is retained in the
[validation summary](../evidence/07-v3-validation-summary.json). The v3-selected
primary and reference were seasonal-residual baseline capacity versions with
0.10 and 0.075 sale fractions respectively, both using a 0.01 return fraction.
They achieved validation precision 0.9714/0.9677, recall 0.85/0.75 and episode
recall 1.0. They share their feature recipe and trained native forest parameters;
the distinct baseline thresholds are the versioned behavior. This is not a claim
of independent training data. The multiscale IF passed several metrics but its
required high-severity precision was null, so it is not selected for promotion.
All original numerical and sample gates still apply to the frozen final test.

Selection freezes the complete six-case public lineage, temporal cutoffs,
model/config/fit hashes, validation comparison, numerical quality/sample gates
and actual time before opening final scoring and labels. Saved predictions are
recomputed from the actual model and numeric input rows. Original fit times
remain original; compatibility refresh records a new smoke check independently.

## Closing the stage

The passing frozen final experiment supplies two sealed qualification capsules.
The [local OCI receipt](../evidence/07-ready/oci-acceptance.json) records actual
MLflow registration, controlled promotion, rejection, crash recovery, concurrent
recovery, exact rollback, complete atomic PostgreSQL publication and scoped
read-only anomaly/model/evaluation HTTP acceptance. SIGKILL/restart retained
all 1232 results and the exact model/image/release pins. It uses pinned
PostgreSQL 16, an actual built application image and owned services.
The source and consumer Required CI must still pass on the actual final revisions.
The CI reproduction pins the original frozen qualification source revision;
the later source publication correction only refreshes reviewed fast-path
compatibility pins, with 58-table/CSV/context/ledger parity retained.
Public GitHub publication awaits the user's explicit repository-visibility approval.

Read APIs expose factual residual/context explanations and honest stale
historical inputs. This experiment is synthetic and bounded. Broker durability,
ACK/DLQ and production RetailOps read-model integration remain AI 10 work.

A public evaluation projection has an ID derived from the original quality ID,
registry version and MLflow run. Its `quality_id` retains the original frozen
report identity. Re-registering the same qualified fit therefore does not
conflict with an earlier immutable projection or claim another independent
training experiment. Model metadata links to the version-specific projection.
