# Development anomaly detectors 1.0

`anomaly-detectors-1.0.0` consumes the independently verified
[qualified residual inputs](qualified-anomaly-inputs.md). It implements a
seasonal-residual detector and a genuine scikit-learn Isolation Forest, with a
frozen temporal recipe and portable numeric model artifact. This is development
mechanics acceptance. Model quality is `not_evaluated`, detector and operational
serving readiness are `not_qualified`, and the final portfolio test is unopened.

## Temporal recipe and coverage

The required protocol explicitly lists sorted unique series and nonoverlapping
train, validation and development-test business-date windows. It also freezes
the feature policy, training cutoff and selection cutoff. The training cutoff
precedes the first validation scoring origin; selection precedes the first test
scoring origin. Only qualified outcomes available by the appropriate cutoff
enter training or validation. A return known after training cutoff is excluded
even when its business date belongs to the train window.

Membership records every requested series/day from train start to test end,
including gaps. Undeclared days, unqualified observations, insufficient history,
cutoff exclusions and gaps retain explicit statuses and reasons. They never
become zero observations or clean negatives. Protocol bounds are 1,000 scopes,
2,000 days and 10,000 requested rows. Training has no label or oracle filter;
private evaluation truth, scenario metadata and injection controls are absent.

## Fit and selection

The baseline score is the absolute standardized seasonal residual already
computed from each series' qualified history. Isolation Forest is fit separately
for each event-type/currency group; IDs, clocks and reasons never enter the
numeric matrix. Its ordered feature subset must come from the eight-field
parent allowlist. Missing optional values use training medians, or explicit zero
fills when the entire training column is missing; each selected field also has
a missing indicator. Validation and test cannot change these fills or trees.

The frozen policy includes features, seed, tree count, max samples, max features
and contamination. This slice accepts one explicitly configured recipe and
does not select a winning configuration or assert detector quality. Isolation
Forest uses one process/thread, no bootstrap and the locked library version.
Its positive score is the negated native `score_samples`; native offset is
recorded but does not set the validation-selected operational threshold.

Each family's threshold is chosen only from eligible validation scores, using
an explicit unlabelled alert-capacity policy. Defaults allow at most 5% alerts
and 1% high alerts in that validation sample. The integer budget is rounded
down. Comparison is strictly greater than the chosen score, so ties cannot
exceed capacity and a zero budget yields no validation alerts. These capacity
settings are development policy, not evidence of useful precision or recall.
At least 16 eligible validation rows are needed per family/group; a forest also
needs at least 16 training rows. Validation diagnostics distinguish unready
inputs from an unfitted forest. Development-test predictions abstain with null
score, threshold, alert and severity when prerequisites fail.

Scored predictions retain observed/expected/residual values and explanation
codes. Promotion and inventory constraint are context, not causal explanations.
The seasonal score and IF score are separate families; their numeric scales
cannot be compared as probabilities.

## Artifact, reconstruction and bounds

The model is JSON numeric tree data, with train-only fills and validation
thresholds; it is never pickle. Graph validation rejects cycles, shared children,
orphans, inconsistent sample counts, invalid feature indices and excess depth.
Inference casts inputs to float32 while comparing against double tree thresholds.
Path arithmetic uses 50-digit Decimal and rounds scores to 12 decimal places.
Every fit checks portable/native agreement within `1e-12`, including float32
values adjacent to actual tree root splits.

Each fit has fixed 60-second wall/CPU and 512-MiB RSS limits, at most 10,000
training rows and 4 MiB of numeric matrices. The parent monitors and kills the
worker on a limit breach; final worker measurements are checked again. Actual
resource measurements stay in acceptance receipts, outside deterministic IDs.

An artifact contains exactly `model.json`, `membership.jsonl`,
`validation_scores.jsonl`, `test_scores.jsonl`, `run_manifest.json` and
`manifest.sha256`. The model is bounded to 8 MiB and row files to 32 MiB each.
The model ID binds parent feature identity, protocol, policy, runtime, training
membership, fills, trees and validation thresholds. The run ID additionally
binds all membership, validation and test bytes and status counts. Publication
is immutable and atomic in a separate `data/generated` root. Parent overlap,
symlinks, hardlinks, changed reads, missing/extra files and altered seals fail.

Verification first checks identity and hashes, then independently verifies the
native parents, refits preprocessing/forest, selects validation thresholds and
reconstructs every output. Re-sealing altered predictions does not make them
valid. This offline verifier is intentionally more expensive than inference.

```sh
retailops-ai-anomaly-detectors build \
  --feature-dir /path/to/qualified-anomaly-inputs \
  --replay-dir /path/to/full-dq-replay \
  --coverage-dir /path/to/day-coverage \
  --curated-dir /path/to/curated --import-dir /path/to/snapshot \
  --protocol-file /path/to/protocol.json \
  --generated-root /separate/workspace/data/generated

retailops-ai-anomaly-detectors verify \
  --directory /separate/workspace/data/generated/anomaly-detectors/ID \
  --feature-dir /path/to/qualified-anomaly-inputs \
  --replay-dir /path/to/full-dq-replay \
  --coverage-dir /path/to/day-coverage \
  --curated-dir /path/to/curated --import-dir /path/to/snapshot

make anomaly-detectors-check
```

The optional build `--policy-file` supplies an explicit nondefault recipe.
Verify reads the frozen protocol/policy from the artifact. Packaged JSON schemas
are checked by `make contracts-check`.

The required native gate runs two fresh isolated processes, each rebuilding both
public profiles through snapshot, curated, DQ, qualified features, detectors and
independent verification. All six artifact files must match between processes;
all parent bytes remain unchanged. Each process is limited to 300 seconds and
1,024 MiB. A further fresh process scores the saved models with NumPy/SciPy/
scikit-learn imports blocked. These small, single-seed public transport fixtures
prove mechanics only.

AI07 remains open: frozen observation/episode evaluation with coverage and
negative cases, larger partitioned development/training data, three source
seeds and scenarios, validation-led model comparison, and anomaly-specific
registry, pinned batch inference and read-only publication/serving.

The [mechanics acceptance receipt](../evidence/ai/07/07.8-detectors/README.md)
records measured resources, native/installed reproduction and the remaining scope.
