# AI07.8 — frozen development detector mechanics

The [detector contract](../../../../reference/anomaly-detectors.md) implements
seasonal-residual and genuine Isolation Forest scoring from verified native
qualified inputs. Temporal membership uses business dates and outcome availability;
requested absent days remain unknown. Train-only fills/trees and unlabelled
validation-capacity thresholds are frozen before development test. No private
truth or final portfolio test was opened.

The [structured receipt](verification.json) records **37 targeted tests** and
four fresh complete profile processes: demand/physical through the editable
package, then both through an installed wheel. All six artifact files match
between the two independent rounds. The processes passed in **179.143 / 140.553 / 131.616 / 135.823 seconds**;
peak RSS was **378.781 MiB**. Each process retains 300-second / 1,024-MiB limits.
All native source, curated, DQ, qualified-feature and model reconstruction checks
remain required. An earlier two-profile process exceeded its budget; measured
reconstruction took about 215 / 204 seconds per profile. Acceptance now uses
one complete profile per process, twice, without dropping rows or parent checks.
The required CI gate independently repeats this four-process protocol.

Each forest fit used at most 139.562 MiB and 3.042 seconds, within
fixed 60-second / 512-MiB limits. Portable/native score discrepancy is below
`1e-12` after 12-place rounding. Fresh data-only readers block NumPy/SciPy/
scikit-learn imports and reproduce 818 demand / 806 physical test scores.
Each family scores 409 / 403 requested days and abstains on 1,319 / 1,325.
These are mechanics/coverage counts, not precision, recall or clean negatives.

Tests cover future/test leakage, late return outcomes, missing declarations,
corrupt tree graphs, NaN/overflow, resource-limit worker termination, aliases
and re-sealed prediction corruption. A genuine IF alert with zero residual
retains `zero_residual`, without claiming observed units exceeded an expected
range. Installed CLI build/verify matches all artifact bytes and also rejects
re-sealed corruption after native parent/model reconstruction. All parent bytes
remain unchanged.

Lint, formatting, strict typing, documentation, contract snapshots, package,
Compose configuration and secret scans passed locally. Full regression and
isolated Docker/PostgreSQL/MLflow persistence run in final-head Required CI;
exact results are attached to the draft PR after completion. The checks budget
remains 125 minutes, with every existing gate retained. No shared daemon,
service, AI05 worktree or other session was changed.

Public single-seed transport fixtures qualify mechanics only. AI07 remains open
for frozen observation/episode metrics and coverage, larger partitioned data,
three source seeds/scenarios, model comparison, anomaly lifecycle, pinned batch
inference and read-only publication/serving.
