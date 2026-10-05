# Frozen operational raw DQ fixture

`raw-dq-v1.zip` SHA-256:
`4640ed308864d872715cc32f6a055e3e18238fe5bd268e97734860d249472e06`.
It copies only `raw/events.jsonl` and `source_binding.json` for each demand and
physical case from accepted producer runtime
`b59aca8e2fe70a76efde2192b63b5bf839104bac` in RetailOps Cloud Native Platform.
No producer runtime, private plan, labels or expected replay is included.

Parents are the exact public snapshots in [anomaly-v1_2.zip](anomaly-v1_2.md).
[Machine-readable lineage](raw-dq-v1.lineage.json) binds producer fixture IDs,
copied file bytes/hashes and reference operational replay hashes. The reference
hash covers every operational section except report (consumer policy version
differs). It is only a test oracle; normal replay never reads it or producer
replay. Each section was compared directly against accepted producer output,
without joining fault truth. Selection/projection is independently recomputed
from curated. This selected sample cannot qualify full daily/return coverage
or model quality.
