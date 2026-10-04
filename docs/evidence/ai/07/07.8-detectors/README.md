# AI07.8 — frozen development detector mechanics

The [detector contract](../../../../reference/anomaly-detectors.md) implements
seasonal-residual and genuine Isolation Forest scoring from verified native
qualified inputs. Training and validation membership use both business dates
and outcome availability; absent requested days remain unknown. Train-only
fills/trees and unlabelled validation-capacity thresholds are frozen before the
development-test window. No private truth or final portfolio test was opened.

The [structured receipt](verification.json) records 36 targeted semantic tests,
two fresh editable native processes covering both public profiles, an installed
wheel native process and installed CLI build/verify. All six artifact files match
across the independent runs. Both fresh editable processes passed in
188.005 / 189.876 seconds; the installed wheel passed in 297.852 seconds.
Peak parent-process RSS stayed below 513 MiB. The unchanged acceptance limits
are 300 seconds / 1,024 MiB. Each forest fit used at most 147 MiB and about
1.5 seconds in the editable proof, within fixed 60-second / 512-MiB fit limits.
Native/portable score discrepancy is below `1e-12` after 12-place rounding.

Fresh data-only readers, with NumPy/SciPy/scikit-learn imports blocked, reproduced
818 development-test scores for demand and 806 for physical. Each family
scored 409 / 403 requested days and abstained on 1,319 / 1,325, respectively.
These are mechanics/coverage counts, not precision, recall or clean negatives.
Negative tests include future/test leakage, late return outcomes, missing
declarations, graph corruption, numeric NaN/overflow, resource-limit worker
termination, aliases and re-sealed prediction corruption. The installed CLI
also rejects re-sealed corruption after native parent reconstruction.

Lint, formatting, strict typing, documentation, all contract snapshots, package
construction, Compose configuration and both secret scans passed locally.
The full regression suite and real Docker/PostgreSQL/MLflow persistence run in
Required CI for the final head; their exact results are attached to the draft
PR after completion. The whole checks budget remains 125 minutes and every
existing gate remains required. No shared Docker daemon or service was started.
AI05 and other sessions' worktrees remain outside this change.

Public single-seed transport fixtures qualify mechanics only. AI07 remains open
for frozen observation/episode metrics and coverage, larger partitioned data,
three source seeds/scenarios, validation-led model comparison, anomaly lifecycle,
pinned batch inference and read-only publication/serving.
