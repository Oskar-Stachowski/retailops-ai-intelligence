# Frozen source 2.8 / anomaly snapshot 1.2 fixture

Archive: `anomaly-v1_2.zip` (5,625,801 bytes; 9,743,177 decoded bytes).
SHA256: `c344a5cfeea086e1054add724c830d747815e9eab425a19a17c1d0691671066c`.

Producer runtime: `b59aca8e2fe70a76efde2192b63b5bf839104bac` in
`retailops-cloud-native-platform`, branch `ai/07-anomaly-handoff`.
Seed 42; `ai-smoke`, 30 days, 8 products, 3 stores, 2 warehouses. These bounded
fixtures have separate demand and physical plans; they are not a composite
five-injection dataset.

| Case | Source ID |
|---|---|
| Demand | `source-sha256-ccadc8f75527261bbcc187383269c4a3f82b7f75290c0baf1e61adbc73fc87e8` |
| Physical | `source-sha256-0eb05ee1e8b903de4c318703ecc74708b0f7ca9568b0c8e4e479684f13521721` |

Each has `public/` with 43 facts tables and `private/` with 55 tables and separate
evaluation artifacts. Private imports always require explicit opt-in. Source
facts/38 hard gates pass; model and overall AI07 readiness stay false.
The [acceptance evidence](../../docs/evidence/ai/07/07.4/README.md) records all four
snapshot IDs, manifest hashes and detached-wheel verification. Producer source
CSV/normal facts and independent raw DQ packages are not embedded in this archive.
