# Anomaly snapshot 1.2 import

The file importer supports producer source 2.8 through snapshot 1.2. Contract
copies live in `contracts/source_snapshot/v1_2` and are packaged in the wheel.
No producer checkout, generator, database or running AI05 service is required.

Verification checks reviewed contract bytes, 38 source hard gates, source and
qualification identities, typed logical hashes, Arrow metadata, table grains,
native row schemas, ledger conservation and projected daily inventory snapshots.
The public variant has 43 operational tables and an exact metadata/file
allowlist. It carries no injection plan, effect labels or qualified truth windows.

The private variant has 55 tables and separate evaluation artifacts. Every
verify/import/reimport requires `--allow-evaluation-truth`. Private scenario and
configuration copies must match their parent artifact references and descriptor
hashes. Plan instances must pass the reviewed demand or physical JSON schema.
The consumer does not regenerate the producer process or assert causal truth;
that check belongs to the producer's source/private snapshot acceptance.

```sh
uv run --locked --extra snapshot retailops-ai-snapshot import \
  --snapshot-dir "$SNAPSHOT_DIR" --require-use-case anomaly_source \
  --generated-root data/generated
uv run --locked --extra snapshot retailops-ai-snapshot verify-import \
  --import-dir "$IMPORT_DIR" --require-use-case anomaly_source
```

Import seals a bounded private copy, checks it again and publishes an immutable
receipt under `data/generated/source_snapshots`. Reimport reuses the publication
without changing input or previously published files. The frozen
`data/fixtures/anomaly-v1_2.zip` exercises demand and physical sources in public
and private forms in CI, independently of the producer implementation.

This milestone qualifies transport/import only. Curated 1.0/1.1 builders reject
snapshot 1.2 explicitly. Anomaly curation, offline DQ integration, PIT-safe
expected/residual features, detector training/evaluation and MLflow/serving remain
the next AI07 work. No existing forecast/model receives readiness for these new
source IDs merely because import succeeds.
