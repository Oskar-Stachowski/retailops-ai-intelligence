# AI07.7 — receipt-qualified anomaly residual inputs

The [versioned feature artifact](../../../../reference/qualified-anomaly-inputs.md)
queries the day gate at two separate clocks: all 28 historical days at the UTC
start-minus-one-microsecond fit cutoff, and the evaluated sales/return day at its
exclusive UTC end plus 24/72 hours. Only available accepted native facts supply
units. Event type and currency remain separate; explicit zero, unknown DQ,
unavailable closure, closed location and insufficient history retain their meaning.

The [structured receipt](verification.json) pins runtime
`d5d88d93704f7c763a64203d55c68f6fdb5b3593fc8a1792fe013fa8ece7aae3` and unchanged qualification runtime
`3a9109c2e252363c9d058a973885ef16af569ba74e1b0cefb7428acab9f00c3b`. The complete local suite passed with **2037 tests**,
including the 34 new semantic/independent-native artifact cases. Tests exercise
fit and scoring boundaries, late receipt/closure, later repair, outcome exclusion
from expected/scale, rejected/refunded returns, currency isolation, fit-known plans,
scoring-known stock, a fixed numeric model allowlist, quarantine withholding,
resealed corruption, aliases and unsafe publication roots.

Fresh editable processes took 172.394 / 192.652 seconds; fresh installed-wheel
processes took 135.520 / 126.385 seconds. Peak RSS was 325.453 MiB. Every
process covered both public profiles and reproduced identical IDs and all three
artifact checksums within the unchanged 300-second / 1024-MiB limits. The wheel
uses exact locked dependencies; installed CLI build/verify also passed against
unchanged public parents. There are no producer modules or private evaluation
truth in the isolated acceptance processes.

All acceptance/contract gates, packaging, Compose configuration and both secret
scans passed locally. The initial `make ci-local` stopped at the pre-existing
return-view resource gate after all tests and the new feature proof passed.
Repeating that gate passed in 180.297 /
159.365 seconds with unchanged native view
IDs/content. The previously unfinished targets then passed in a separate make
invocation. No passed test or acceptance gate was dropped and the 300-second /
1024-MiB limits were retained. Failed return checks now report measured time/RSS.
Required CI for the exact final head, including real
isolated Docker/PostgreSQL/MLflow persistence acceptance, is recorded in the draft
PR after completion. The previous qualification head's PR checks took 98:20;
the new tests and two fresh processes increase the whole checks budget from 105
to 125 minutes while retaining every gate and the per-process resource limits.
No shared daemon, service, AI05 worktree or other session was changed.

The public 30-day/single-seed source and replay fixtures prove input mechanics,
not detector quality. Return scope remains `purchases_in_parent_source_only`.
Stock context reports potential censoring without imputing demand or creating an
automatic alert. `ready_input` describes calculable residual inputs, while
`detector_readiness` remains `not_qualified`.

AI07 still needs the seasonal-residual baseline and Isolation Forest comparison,
validation-selected thresholds, frozen observation/episode/scenario/seed evaluation
and its detector lifecycle/read-only publication gate.
