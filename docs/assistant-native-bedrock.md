# Native Assistant Bedrock runtime

`ASSISTANT_NATIVE_RUNTIME_FILE` selects `assistant-native-bedrock-v1` with the
eight native read adapters and qualified semantic retrieval. This mode requires
`RAG_BEDROCK_ENABLED=true`, an AI database, a SELECT-only Producer connection,
the Source import, Curated dataset, full DQ replay, day coverage and a private
access policy. These settings remain server-owned and excluded from settings
serialization. Document, native offline and native Bedrock modes are mutually
exclusive. The configuration follows
[the strict schema](../contracts/assistant/v1/native-bedrock-runtime.v1.schema.json).

The runtime binds the current application checksum, exact graph, accepted
question routes, Source catalog and dataset pins, and the active qualified
AI11 retrieval index. It rejects proposed routes, mixed environments, fake
retrieval, stale code and the unaccepted native-v2 suggestion transport.
The inherited document rules must refer only to chunks inside that exact index.
Forecast/model reads retain the canonical namespace; an accepted development
namespace in AI10 does not establish production quality.

Bedrock chat initializes lazily after durable Assistant admission. Readiness and
dependency failure create no AWS client. Business evidence keeps its complete
wire context; documentation and verified-state requests use the existing
question-bound compact projection for both token counting and generation.
Full evidence validation remains on the server. Chat retains the pinned token,
cost, retry, deadline and circuit limits. Titan query embeddings have separate
request and aggregate UTF-8 byte budgets, with a conservative priced upper
bound checked against the embedding cost cap. SDK inference retries are disabled.
No corpus rebuild or new corpus embeddings are implied by enabling this mode.

## Unpaid integration

[The separate CI workflow](../.github/workflows/ai12-source-prepaid.yml) checks
the original AI12 Assistant API, native operations SQL reader, atomic AI outbox,
locked Confluent publisher, actual Redpanda broker, original pinned Source
checkpoint consumer, Source PostgreSQL and authenticated API, and its built UI
in Chromium. It also checks identical duplicate bytes, one business row, scope
denial, read-only evidence, absent execution controls and live credential
revocation. It provisions and cleans only its own containers and processes.

The LLM is explicitly scripted and the observed sales failure is invented in
the real Source database. Source v1 transport remains its explicit fixture opt-in.
This qualifies transport and integration mechanics, not real LLM answer quality,
production observations or a new Source transport policy. No AWS SDK client is
allowed in the emitter helper. Local shared Docker and Compose are not started.

## Stop before paid tests

The current `.prepaid.v4` graph, release and smoke candidates preserve earlier
published families. The `bedrock-smoke` command prepares a proposal by default;
paid calls require `--execute`, an exact cost cap and a new private durable output.
The Haiku/Sonnet fixture smoke and document smoke are separate from native
Assistant acceptance on real Source data. Inspect their existing limitations
before using their results as evidence.

The 50 golden cases and 26 routes still require independent semantic review.
The new runtime deliberately refuses proposed routes. AI11's independently
accepted retrieval labels do not accept this different Assistant set. After
review, create a separately identified accepted route/configuration artifact;
do not edit historical receipts or label a fixture as human approval. New paid
inference and embeddings remain withheld until the user resumes that work.

[The paid qualification runbook](ai12-paid-qualification.md) binds the prepared
proposals, review packet, separate native campaign and remaining evidence gaps.
