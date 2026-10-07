# AI12 suggestion emitter checkpoint

Assistant now has an opt-in transactional emitter for persisted human-review
suggestions and a separate worker with stable event IDs, immutable wire bytes,
expiry checks and delivery receipts. [Configuration](../assistant-suggestion-outbox.md).

The local acceptance runs against an owned PostgreSQL 16.15 / pgvector 0.8.6
cluster with migration `0028_ai12_suggestion_outbox`. Its 19 SQL cases cover:

- Atomic HTTP completion and default-disabled transport.
- Rollback of answer, candidate, terminal run and outbox after an enqueue fault.
- Expiry before selection and during validation, with no producer call.
- Invalid/missing/duplicate ACKs, pending delivery and invalid broker positions.
- Identical-byte retry after failed delivery or an ACK followed by SQL failure.
- Competing workers, duplicate enqueue and immutable identity collisions.
- SQL origin enforcement, lifecycle immutability, independent retention and
  refusal to downgrade while events are retained.

Callbacks are test doubles and make no socket connections. This qualifies the
delivery mechanism, not a live broker or Source consumer. Native Assistant's
separate SQL/HTTP acceptance also checks a persisted operations outbox record.
The independently copied Source v1 fixture validates under both Source's schema
and the current emitter schema. Native-v2 suggestions are rejected rather than
downgraded; their Source policy acceptance remains open.

The new `.suggestion-outbox.v1` candidate family keeps every previously
published family, golden label and owner receipt unchanged. Source owner pins,
model modules and dependency locks retain their published bytes. The
[machine-readable receipt](12-suggestion-outbox.json) records the final checks
and their limits. PR32 remains draft and AI12 remains **in_progress**.

Open acceptance: independent question labels and native review policy, current
Source/model publication, Source consumer/UI delivery, operational observations
and AWS-backed LLM/RAG quality. No neighboring worktree, shared service, model
fit or AWS environment was modified.
