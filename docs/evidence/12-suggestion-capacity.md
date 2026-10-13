# AI12 suggestion outbox capacity acceptance

The real private PostgreSQL acceptance now passes **20/20 cases**. The new
boundary case fills `ai.assistant_suggestion_outbox` to 1000 retained events,
rejects event 1001, accepts an identical retry without adding a row, and rejects
different bytes under the same immutable identity at full capacity.

The fixture starts from an actual successful Assistant HTTP result. Copies are
validated as Assistant run, answer and persisted suggestion contracts and pass
the required SQL `running` → answer → `succeeded` transitions. Database guards
remain enabled. A surrounding transaction rolls back every fixture origin and
outbox event; counts of all four tables must match their baseline afterwards.
This qualifies the SQL capacity and identity guards, not HTTP admission or
throughput at 1000 events. Producers remain explicit callbacks without a broker.

The test joins the existing `native-offline-smoke` persistence gate. Complete CI
collection finds 4835 cases in 219 files, with every case assigned once across
four shards. Collection is distinct from executing all those cases.

Local `ci-checks`, types, contract pins, golden 50/50 and 36/36 critical, build,
and Compose configuration checks pass. The owned PostgreSQL cluster is stopped.
Application Python, all 95 published agent files, Source pins, event contracts,
dependency locks and historical receipts retain their exact published bytes.
No additional candidate family is needed for this test-only change.

The [machine-readable receipt](12-suggestion-capacity.json) preserves the exact
application-head CI result and independently downloaded native/outbox SQL
artifacts. Application head `e671d73` completed
[Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37676309071)
with **17/17 successful jobs**, including both persistence jobs. The follow-up
test/documentation head needs its own CI execution.
Historical [emitter acceptance](12-suggestion-outbox.md) remains unchanged.

AI12 stays **in_progress** and PR32 stays draft. Live Source consumer/UI delivery,
native-v2 policy and independent question-label acceptance, current Source/model
publication, scoped operational observations and AWS-backed LLM/RAG quality
remain open. No AWS, AI10 service, broker or neighboring session was operated.
