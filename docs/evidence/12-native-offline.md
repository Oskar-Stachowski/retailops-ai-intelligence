# AI12 native offline runtime checkpoint

The test-only runtime wires the eight-tool catalog to the existing Assistant API
and durable store, using fake chat and fake-vector PostgreSQL retrieval. The
explicit v2 stockout review proposal preserves scoring origin and observation
expiry. [Configuration and limits](../assistant-native-offline.md).

The checkpoint preserves every published `.native-tools`, `.native-inventory`,
`.native-sources` and `.native-v12` artifact. Current evaluation and smoke checks
use the new `.native-offline.v1` family. Canonical golden labels and original
model-quality receipts remain unchanged. No AWS, training, full Source export,
AI10 event delivery or neighboring-session process was invoked.

Completed local checks:

- 399 agent/Assistant regression cases.
- 158 forecast partition/outcome/journal cases, covering the manifest envelope
  integration regression found by the previous PR CI shard.
- After main integration: 124 configuration, native review, scripted document,
  Source compatibility, CI contract and AI09 generation cases.
- `ci-checks`: lint, types, contracts, documentation, frozen fake evaluation,
  package build and Compose configuration.
- Frozen golden: 50/50 cases and 36/36 critical cases, with fixture-only evidence.
- Built wheel imported independently with sockets blocked: the same 50/50 and
  36/36 results; offline runtime and all native adapter modules are packaged.

The 128 KiB partition manifest envelope retains the complete installed-module
pin. The former 64 KiB bound was exceeded after independent adapter modules
were added. Preparation and verification use the same finite bound; no source,
label, physical membership, runtime pin or model gate was removed.

The dedicated SQL/HTTP acceptance uses an owned PostgreSQL 16.15 cluster with
pgvector 0.8.6, migration `0027_ai10_ai12`, isolated AI and fixture Producer
schemas, and a SELECT-only Producer runtime role. The acceptance passed:

- Six successful HTTP runs (sales, inventory, anomalies, operations, model and
  documentation), six persisted answers and one persisted operations review suggestion.
- Forecast/risk horizons outside the fixture calendar are rejected with HTTP 422;
  direct canonical SQL readers preserve missing outputs.
- Authorized traces survive API recreation; native and knowledge revocations
  hide them after policy restart.
- Actual Producer writes are denied. Non-UTC sessions and a changed index pin
  fail readiness. Raw index pointer tampering is rejected by PostgreSQL itself;
  the drift check uses a legitimate audited activation of another test index.

Successful HTTP runs took 0.40–0.49 seconds against a 45-second deadline on this
small fixture. This is a mechanics measurement, not a production latency claim.
The [machine-readable receipt](12-native-offline.json) records code, log and
package hashes, complete coverage and each acceptance limitation.
The Docker reproduction is attached to
Required CI's persistence job; the local Docker daemon remains stopped.

Main `16a02aecbc4f9558e8222c02ad5a24d82745545b` is integrated in commit
`67c9d4e`. All 71 agent files published at `3d4fe92` and historical dependency
locks remain byte-identical. The Source owner manifest and 83 shared campaign
files match current main. All 590 Python files and both runtime locks match the
built wheel; direct wheel execution passes the golden with sockets blocked.
CI collection includes 4777 cases in 215 files, assigned exactly once across
four shards. Collection does not execute the whole repository suite.

The immutable July Source fixture cannot authorize October forecast/risk HTTP
horizons. These must fail scope validation. Canonical SQL readers are checked
separately for missing-output behavior. Positive native model publication and
semantic/LLM quality are separate gates; the scripted renderer is not an
independent answer-quality evaluator.

AI12 remains **in_progress**, and PR32 remains draft. Independent question-label
review, native review-policy acceptance, actual current Source/model publication,
AI10 delivery/operational observations, and AWS-backed LLM/RAG qualification
remain open.

The previous published head `3d4fe92` failed Required CI with 15/17 successful
jobs because its partition envelope exceeded 64 KiB. The 158-case regression
covers the corrected 128 KiB bound. The new published head requires its own CI;
the previous failed run is not claimed as successful acceptance.
