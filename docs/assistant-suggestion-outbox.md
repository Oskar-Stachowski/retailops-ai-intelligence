# AI12 human-review suggestion outbox

When `ASSISTANT_SUGGESTION_OUTBOX_ENABLED=true`, successful Assistant completion
persists the answer, review candidates, terminal run and delivery records in one
SQL transaction. An enqueue failure rolls all of them back and returns a bounded
HTTP 503. The flag defaults to false and requires the isolated AI database.

Migration `0028_ai12_suggestion_outbox` follows the unchanged published migration
history. Its `ai.assistant_suggestion_outbox` checks persisted Assistant origin,
environment, payload identity, exact wire bytes and SHA-256. It stores at most
1000 retained events per local/test environment. Events expire with their review
candidate; terminal records are retained until 900 seconds after creation.
Assistant retention cannot cascade away this independently stored delivery copy.
Downgrade refuses a nonempty outbox.

The envelope uses `recommendation_generated`, schema `2.0`, topic
`retailops.intelligence.v2`, the shared UUID namespace and the existing grain
partition key. Its payload is checked against the independently pinned
[Source v1 projection contract](../contracts/events/suggestion-source-v1/upstream.json)
at Source commit `090c26307fd508899ad6ef24504c151875db820b`.
Only `read-only-review-v1` is transportable. The proposed native-v2 policy adds
observation expiry; it remains local until Source accepts that contract. A v2
candidate is rejected by the emitter and cannot be relabeled as v1. Native
offline startup rejects enabling transport with a v2 graph.

The separate delivery environment retains its existing Kafka lock. After the
consumer contract and broker have been independently qualified, the worker is:

```sh
uv run --locked --project tools/intelligence-delivery python scripts/intelligence_outbox.py \
  --env-file .local/assistant.env --broker-config .local/broker.json \
  --suggestions --max-events 100
```

The broker file must be owned by the current user with mode 0600. Each worker
checks the AI SQL role/database, migration and UTC, locks one live pending record
with `FOR UPDATE SKIP LOCKED`, and checks expiry immediately before producing.
Exactly one valid delivery callback is required before the SQL delivery receipt.
An ACK followed by a SQL crash can repeat the same event ID and original bytes;
the consumer must deduplicate by event ID. Delivery is at least once. Broker
ACK does not prove Source projection or human approval.

The [local acceptance](evidence/12-suggestion-outbox.md) uses real private
PostgreSQL and explicitly scripted callbacks. No broker, AI10 process or AWS
service was started. Required CI runs this acceptance beside native Assistant
SQL and preserves both safe receipts. AI12 remains in progress.
