# AI07.4 — curated and demand input acceptance

This acceptance continues the [transport milestone](../07.4/README.md). Runtime
code and wheel were frozen at AI commit `277e2c57cf9bbb34fded95854413e256be68dc52`.
The unchanged source fixtures retain producer runtime
`b59aca8e2fe70a76efde2192b63b5bf839104bac`; their source/snapshot identities are
listed in [verification.json](verification.json). No generator or AI05 service
was used by this slice. AI07 remains open.

## Accepted behavior

Curated 1.2 retains all 43 operational tables, native physical grains, quantities,
exact money, availability and revisions, with parent qualification identity.
Private truth remains in the separate import and needs explicit opt-in for
curation. Public/private operational curated tables and feature bytes match.
Curated anomaly readiness remains `not_ready`.

Daily demand features use separate fit/scoring clocks, seven-day seasonal-naive
expected and a robust scale fitted only on prior known seasonal residuals.
Evaluated outcomes and later revisions do not change their expected/scale.
Zero MAD has an explicit one-piece floor. Closed, unavailable, invalid and
insufficient-history points do not have a residual/standardized residual.
Price/promotion context is known before the window; stock context is available
by scoring origin. Raw DQ completeness is explicitly not qualified.

The normal input allowlist contains four operational tables. Injection labels,
parameters and seeds are excluded. A changed scoring policy changes the immutable
identity. Verification independently reloads the parent and recomputes all rows;
resealing a false residual or an early curated inventory timestamp is rejected.

## Physical evidence

| Public fixture | Points | Ready demand inputs | Insufficient data | Closed | Feature bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| demand | 678 | 258 | 360 | 60 | 2547588 |
| physical | 678 | 258 | 360 | 60 | 2547686 |

Two isolated editable processes reproduce all IDs/content and retain source and
publication bytes. Two additional detached installed-wheel processes reproduce
the same results; every imported `retailops_ai` module is from the installed wheel,
and the producer namespace is unavailable. Each process checks both public cases,
build → rebuild → full parent replay. Timings are 52.2–54.0 seconds and peak RSS
106.6–111.6 MiB, below 300 seconds/1024 MiB per process.

Generated wheel acceptance artifacts remain outside Git under
`/private/tmp/ai07-inputs-wheel-acceptance/run1` and `run2`. The frozen zip and strict
packaged contracts make the checked mechanics reproducible on a clean checkout.
Full-profile performance and model effectiveness are not inferred from these
30-day, one-seed transport fixtures.

## Reproduction and validation

```sh
uv sync --locked --extra snapshot --extra forecast
uv run --locked --extra snapshot --extra forecast python -m pytest -q \
  tests/test_anomaly_features.py tests/test_anomaly_inputs.py \
  tests/test_anomaly_snapshot_import.py tests/test_inventory_curated.py
make anomaly-inputs-check
uv build --no-build-isolation
```

The verification receipt records targeted tests, the full regression result,
static/contract checks, installed wheel checksum, policy limits, exact artifact
IDs and each process's measured timing/RSS. Runtime formulas, identity and clock
semantics are described in the [runbook](../../../../reference/anomaly-inputs.md)
and [data card](../../../../cards/anomaly-inputs.md).

Local full regression passed 1217 cases in 922.89 seconds. Two additional
curated semantic counterexamples, added after that run's collection, passed
separately for both source variants (1219 unique cases total). Ruff/format,
full Mypy (199 modules), documentation links and all existing wire contract
snapshot checks passed. The remote Required CI checks the final PR head.

The initial remote persistence job exposed a missing `contracts/anomaly` copy in
the API Docker build context. `Dockerfile.api` now includes it before package
installation. The API image builds successfully; a separate read-only container
without network, ports or volumes reads all four anomaly contracts and the
curated 1.2 schema with matching checksums. The image digest and hashes are in
the receipt. No existing service was started or stopped by that local check.

Raw DQ coverage/integration, return-focused features, evaluation labels/splits,
threshold selection, Isolation Forest, multi-seed observation/episode evaluation
and AI05 lifecycle integration remain unqualified. There are no anomaly alerts,
champion promotion or business side effects in this milestone.
