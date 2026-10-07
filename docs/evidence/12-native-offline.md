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

Completed local checks before the final SQL acceptance:

- 399 agent/Assistant regression cases.
- 158 forecast partition/outcome/journal cases, covering the manifest envelope
  integration regression found by the previous PR CI shard.
- 13 offline configuration cases and 7 native review proposal cases.
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
schemas, and a SELECT-only Producer runtime role. Its final receipt records the
actual result and route/SQL coverage. The Docker reproduction is attached to
Required CI's persistence job; the local Docker daemon remains stopped.

The immutable July Source fixture cannot authorize October forecast/risk HTTP
horizons. These must fail scope validation. Canonical SQL readers are checked
separately for missing-output behavior. Positive native model publication and
semantic/LLM quality are separate gates; the scripted renderer is not an
independent answer-quality evaluator.

AI12 remains **in_progress**, and PR32 remains draft. Independent question-label
review, native review-policy acceptance, actual current Source/model publication,
AI10 delivery/operational observations, and AWS-backed LLM/RAG qualification
remain open.
