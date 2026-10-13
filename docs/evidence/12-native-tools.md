# AI12 — all native read adapters checkpoint

The four remaining adapters are implemented: stockout risk, detected anomalies,
scoped live operations and model status. Together with the previously qualified
sales, inventory, forecast and knowledge adapters, the server-owned factory can
assemble the complete eight-tool catalog. [Behavior and limits](../agent-native-tools.md).

Main `2dc0a5baa601704451d1c5f24d9cb43313cfa975` is integrated in the isolated
`ai/12-resume` branch. Migration `0027_ai10_ai12` joins the existing published
AI10 and assistant histories without changing either historical migration.
Both AI10 replay checks and AI12 offline evaluation remain in CI.

The active unpublished candidate family is `.native-tools.v1`; it binds the
integrated application. Historical candidate families, canonical golden labels,
model artifacts and previous receipts are preserved.

Before main integration, all 99 new native read-tool cases passed. The broad
regression and corrected suite contain 747 unique cases with passing final
outcomes. Four initial routing assertions expected a five-row anomaly budget;
they now check the complete seven-day grid. A separate earlier run was
invalidated by changing configuration during execution and is excluded from
acceptance. The subsequent integrated validation is recorded in the
[machine-readable receipt](12-native-tools.json).

Tests use explicit native DTO fixtures, a fake SQL connection, fake chat and an
in-memory capture store. They check authorization, independent underlying
capabilities, exact scope and physical mappings, complete native evidence,
causal windows, current observations, safe failures, HTTP admission, stored
trace revocation and review-only suggestions. They do not execute real native
PostgreSQL or Producer queries and do not qualify deployed LLM behavior.

The previous inventory head `ec01837` completed
[Required CI 37639653939](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37639653939)
with eleven execution jobs successful. Three execution jobs (persistence,
TensorFlow and source inputs) never acquired a runner after five attempts;
the final gate failed. Those are infrastructure non-starts, not passing checks
and not observed test failures. The new integrated head requires its own CI.

AI12 remains **in_progress**, and PR32 remains a draft. Operations reports
scoped event processing; consumer heartbeat and Kafka lag remain unobserved.
Model status reports scoped v12/anomaly catalog metadata; deployment and drift
remain unattested. Production bindings, native suggestion policy and AI10
delivery, reviewed question labels and budgeted real Sonnet/Titan acceptance
remain open. No AWS call, full Source export, shared Compose startup, model
training or modification of neighboring worktrees is part of this checkpoint.

The Source integration guard uses
`agent/source-bundle.native-tools.v1.json` instead of rewriting the published
AI10 owner pin. The original root lock is retained at
`environments/anomaly/qualification.uv.lock`; original delivery lock/project
copies are retained at `tools/intelligence-delivery/qualification.*`. The active
delivery environment includes the current Assistant dependencies and the same
pinned Kafka client in its isolated lock. Every previous numerical and serving
package version remains unchanged. The 83 shared campaign modules and all
native importer/wire/schema owner copies match integrated main. This guard
qualifies integration mechanics; it does not reclassify scientific evidence.

Integrated validation passed:

- All affected adapter, graph, Assistant, migration, native reader, CI contract
  and AI10 compatibility cases have passing final outcomes; exact unique counts
  and each XML report digest are retained in the JSON receipt. The final Source
  compatibility run passed 180/180 cases using owned loopback servers. Its
  earlier sandbox bind denials and outdated delivery guard are recorded.
- Full `ci-checks`: Ruff on 1241 formatted files, mypy on 711 modules, strict
  native namespace checks on ten owner-copy modules, repository/contract/runtime
  checks, fake golden 50/50 and 36/36 critical, wheel/sdist build and Compose
  configuration validation. Both root and delivery locks pass locked checks.
- Every one of 584 Python modules and both runtime locks matches wheel/checkout.
  A separate process imports the native modules directly from the wheel and
  passes the unchanged golden with sockets blocked and producer namespaces absent.
- Complete CI collection contains 4721 tests in 209 files, assigned exactly once
  across four shards. Collection is not execution of the whole repository suite.
- Tree and history gitleaks pass. The 59 previously published agent files,
  root/TensorFlow/qualification locks, canonical golden and AI10 owner pin are
  unchanged. New head remote CI remains a separate pending acceptance.
