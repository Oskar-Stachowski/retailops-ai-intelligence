# Native Assistant offline integration

`ASSISTANT_NATIVE_OFFLINE_FILE` selects the closed `assistant-native-offline-v1`
runtime in `APP_ENV=test`. It combines the eight-tool native catalog with
`OfflinePolicyChat` and a PostgreSQL index in the `offline_test` lane. Both chat
and embeddings are deterministic fixtures. This tests integration and access
control; it does not qualify language-model answers or semantic retrieval.

The configuration pins the current application checksum, verified Source
catalog, Curated dataset, full Raw DQ replay, day coverage, exact graph and
proposed question routes, and the active index generation. Source readers are
verified at startup and reused by the stockout adapter. The runtime checks the
AI schema, pgvector version, UTC database sessions, active index pin and the Producer read dependency
before a run. A changed index generation makes readiness fail and prevents a
new answer. The original published candidate families remain immutable; current
checks use `.prepaid.v4`.

Set these private settings together:

- `DATABASE_URL`: the isolated `ai_app` / `retailops_ai` PostgreSQL database.
- `API_AUTH_FILE`: the private local access policy.
- `ASSISTANT_NATIVE_OFFLINE_FILE`: a strict JSON document matching
  [the runtime schema](../contracts/assistant/v1/native-offline-runtime.v1.schema.json).
- `ASSISTANT_SOURCE_IMPORT`, `ASSISTANT_CURATED`, `ASSISTANT_REPLAY`,
  `ASSISTANT_COVERAGE`: verified directories matching the configuration pins.
- `ASSISTANT_PRODUCER_DATABASE_URL`: a server-owned connection with SELECT on
  the scoped native `realtime_event_log`.

The offline profile requires `graph.chat.knowledge_mode=offline_test`, fake chat,
fake embeddings, the exact offline index manifest and `RAG_BEDROCK_ENABLED=false`.
Mixed document/runtime configuration and missing native inputs are rejected.
Neither client request nor model output can choose these dependencies. A
Bedrock provider or retrieval-lane index cannot be selected through this mode.
Existing index build, validation, qualification and switch commands provision
the test index; the synthetic corpus review only proves lifecycle mechanics.

The API retains the existing query and safe-trace contracts. Underlying native
read grants and knowledge scope are persisted even when chat is a fixture.
Grants are loaded as a process snapshot: restart all API processes to apply
revocation. Recreated APIs continue to read authorized persisted traces; revoked
native or knowledge access hides them.

## Recorded stockout review proposal

`read-only-review-native-v2` explicitly opts into
`native_stockout_review=true`. The default `read-only-review-v1` remains unchanged.
The proposal can produce a human review candidate from a complete, fresh native
stockout result whose physical inventory evidence and Source lineage are known.
Scored probability must meet the proposed review threshold. A known stockout
with null probability is kept distinct from insufficient data.

`source_as_of` retains the real scoring origin. `evidence_observed_at` records
the verified read time; expiry is at most 300 seconds after that observation,
with the original maximum 24-hour origin age retained. Reading an old result
cannot relabel its origin. Policy, evidence and release pins bind the immutable
candidate ID. No quantity, order, operational writer or deployed-model health
is inferred. This remains a proposed review rule requiring independent acceptance.

## Reproducible local acceptance

With an already running Docker daemon, `make native-offline-smoke` creates one
owned PostgreSQL 16 / pgvector 0.8.6 container, new AI and fixture Producer
databases, a SELECT-only runtime account and a fake-vector test index. It runs
[the SQL/HTTP acceptance](../tests/test_native_offline_postgres.py), then removes
only that owned container. It does not start Compose or global services. Required
CI runs this check in the persistence job. The same acceptance can use explicitly
provisioned disposable databases through `AI12_NATIVE_DATABASE_URL`,
`AI12_NATIVE_PRODUCER_DATABASE_URL`, `AI12_NATIVE_PRODUCER_ADMIN_URL` and
`REQUIRE_AI12_NATIVE_POSTGRES=1`.

The immutable July fixture supports historical HTTP scopes. It cannot authorize
a present forecast or stockout horizon after its channel assignments expire;
those requests must fail before admission. The acceptance separately reads the
canonical forecast and stockout SQL namespaces and verifies that missing outputs
remain missing. The Producer table and failed event are test-owned fixtures;
Kafka heartbeat, broker lag and actual consumer health remain unobserved.

[Acceptance evidence](evidence/12-native-offline.md) records the final checks and
remaining production gates.
