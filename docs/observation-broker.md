# Authenticated observation broker input — AI10

This adapter connects the [durable AI projection](durable-observation-replay.md)
to a real Kafka-compatible broker. It does not create a Source producer or topic,
capture operational Source SQL, hand off all 43 SourceSnapshot tables, or qualify
models. The acceptance publisher is an explicit synthetic observation fixture.

## Run a bounded batch

1. Apply migration `0021_observation_replay` explicitly using the existing AI
   migration procedure. The worker never migrates or creates broker resources.
2. Provision the delete-only, dense, non-transactional topic
   `retailops.source-observations.v1` outside the worker. Compaction, offset holes,
   log rewind and retention that removes unprocessed offsets stop acceptance.
   Transactional/control-record gaps also stop: this bounded lane does not skip
   unseen positions. Preserve the whole log until an approved resync procedure.
3. Give the reader literal Read/Describe/DescribeConfigs permission on that topic,
   Describe on the cluster, and Read/Describe on its dedicated consumer group.
   The reader needs no Write/Create/Delete or producer credential.
4. Create a private, owned, nonsymlink regular JSON file with mode **0600**, at
   most 16 KiB. Set `source_authority_id` to the trusted producer authority UUID,
   `bootstrap_servers` to explicit host:port endpoints (at most eight),
   `username`, `password`, and `ca_file` to an absolute trusted CA path. Only
   `SASL_SSL` and `SCRAM-SHA-256`/`SCRAM-SHA-512` are accepted. No arbitrary
   librdkafka options, automatic topic creation, plaintext or TLS bypass exist.
   Keep credentials outside Git and model artifacts.
5. Run `make intelligence-delivery-bootstrap`, then
   `make observation-consumer ARGS='--broker-config /private/broker.json --group-id retailops-observations-local --max-messages 100 --max-seconds 60'`.
   Supply the existing private `DATABASE_URL` / environment file as documented
   for the AI service. Use a separate group with prefix `retailops-observations-`.
6. The worker discovers the actual cluster and native topic UUID plus the complete
   contiguous partition layout and delete-only policy. SQL pins this identity
   and authority on first authenticated assignment. A range/eager group callback
   claims epochs and seeks to the database/broker-validated position; lost or
   revoked partitions release ownership. One group has one bounded projection.
7. Each record preserves raw value/key, ordered duplicate headers and timestamp.
   Check broker identity before SQL and again before ACK. Commit fact/raw
   receipt/quarantine/checkpoint atomically, then commit **only that delivered
   record's next offset** synchronously and validate the returned receipt.
   A database checkpoint ahead during overlap never ACKs an unread suffix.
8. Any poll, identity, assignment, SQL or ACK failure stops the runner. No later
   message is processed by that instance. Restart explicitly: a committed SQL
   receipt makes broker redelivery safe; a rollback must be processed again.
   SIGTERM/SIGINT release owned leases and close the client; SIGKILL relies on
   SQL rollback/fencing and redelivery. Time/message limits bound each batch.

The CLI emits only completed/count or a fixed unavailable error. It never
prints private configuration, payloads, database errors or native client errors.
The optional Confluent client remains in `tools/intelligence-delivery`; its
isolated dev group uses the same pinned pytest as the core. The core `uv.lock`
and every overlapping runtime/numerical dependency version stay unchanged.

## Mandatory remote acceptance

`make observation-broker-test` owns disposable immutable PostgreSQL 16 and
Redpanda 25.3.6 containers on a GitHub runner. Host ports bind only loopback;
temporary certificates/credentials are removed and only owned containers are
cleaned up. This task does not start local Docker or use another session's stack.

The separate required `observation-broker` job executes twelve actual runtime
checks: group subscription, correction/dedup, byte-exact quarantine and ACK;
SQL capture and historical overlap ACK; SIGKILL before commit and after commit
before ACK; terminated SQL connection; competing lease owner; wrong SCRAM
password and untrusted CA; reader Write denial; foreign topic/group/Create
denials; compaction policy change; and native topic UUID replacement.
The JSON/JUnit artifact accompanies all previous required gates. Unit tests
cover sticky stop, missing or changed ACK receipts and insecure configuration.

Broker identity checks detect observed changes; they do not implement a
distributed atomic transaction between topic administration and PostgreSQL.
Topic replacement requires operator resync and must not run concurrently with
normal ingestion. SQL fencing protects effects; stale workers never gain a
second business effect. Capture remains the AI daily_demand_versions projection,
with corrections selected by available_at/as-of, not operational Source SQL.

The adapter uses the pinned Confluent client’s DescribeCluster/DescribeTopics,
manual assignment and synchronous commit APIs; their primary reference is the
[official Python client API](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html).
