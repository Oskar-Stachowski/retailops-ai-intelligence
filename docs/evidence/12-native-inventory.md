# AI12 — native inventory checkpoint

Implementation `0798cba` introduces verified Source/Curated physical inventory
reads, causal fulfillment routes and complete scoped evidence. Main `a128bb38`
is integrated as `5195e82`; that merge changes neither application Python code
nor runtime locks. [Adapter behavior](../agent-native-inventory.md) and
[machine-readable receipt](12-native-inventory.json) define the exact scope.

Validation passed:

- 61/61 native adapter cases, including the original inventory Source fixture,
  independent ledger totals, parent rebinding, resealed false quantities,
  physical authorization, causal availability, missing/stale observations and
  the HTTP → reviewed planner → graph → stored response/trace path.
- 704 unique regression cases have passing final outcomes. The broad run passed
  685 cases and exposed two new HTTP helper issues; the corrected complete
  61-case adapter file passed without changing application code. The receipt
  retains both reports. The 33-case main integration suite passed; its initial
  six psutil cases required read access outside the macOS sandbox. They launched
  and stopped only owned test processes, with no full Source generation.
- Full integrated `ci-checks`: Ruff, mypy on 657 files, documentation/runtime
  and contract checks, unchanged fake golden 50/50 and 36/36 critical, package
  build and Compose configuration validation. No shared Compose service ran.
- All 538 application Python files and both runtime locks match between wheel
  and checkout. A separate process imports directly from the wheel and passes
  the unchanged golden with sockets blocked and producer namespaces absent.
- Complete CI collection plans 4241 tests across 188 files exactly once in four
  shards, including all 61 inventory, 58 sales and 41 native forecast cases.
  Collection does not imply local execution of the entire repository suite.
- Tree and history gitleaks pass. Historical candidates, normalized golden
  labels, model artifacts and acceptance receipts remain unchanged.

Previous head `8e4e0b1` completed
[Required CI 37634293962](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37634293962)
with **15/15 success**, including OCI, PostgreSQL/MLflow, TensorFlow, every
acceptance group, every test shard and the final gate. The new inventory head
requires its own remote acceptance and does not inherit that result.

Fake chat, a frozen historical test clock and `CaptureStore` are explicit. This
increment does not qualify production wiring, PostgreSQL inventory acceptance,
real LLM quality, native recommendation joins or delivery through AI10 outbox.
AI12 remains **in_progress** and its PR remains a draft.
