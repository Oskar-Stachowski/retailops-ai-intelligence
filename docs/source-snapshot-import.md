# Import snapshotu RetailOps — AI 03.4

Importer przyjmuje wersję snapshotu **1.0.0** i źródło **2.6.0** według
[handoff 1.0.0](source-snapshot-handoff.md). Czyta rzeczywisty Parquet,
przelicza wszystkie byte i typed canonical hashes, a następnie publikuje
niezmienną kopię pod source ID. Nie wymaga repo producenta, jego generatora,
operacyjnej DB, brokera ani AWS. Nie tworzy curated, cech ani modeli.

## Uruchomienie

Osobne CLI pozwala pracować nad importerem i agentem 12 na niezależnych branchach.
Opcjonalne zależności są przypięte w `uv.lock`: PyArrow 25.0.1 i JSON Schema.
API nie importuje automatycznie modułu snapshotów. CI i `make bootstrap`
instalują extra `snapshot`; zwykły pakiet runtime nie wymaga tego extra.

```bash
uv sync --locked --extra snapshot
uv run --locked --extra snapshot retailops-ai-snapshot verify \
  --snapshot-dir data/fixtures/ai-smoke-v1/snapshot
uv run --locked --extra snapshot retailops-ai-snapshot import \
  --snapshot-dir data/fixtures/ai-smoke-v1/snapshot
uv run --locked --extra snapshot retailops-ai-snapshot verify-import \
  --import-dir data/generated/snapshots/source-sha256-a12866e1099c3ae2ae7c73cac5c533a35733618d85a3728cd5f0e1c7b527fc00
```

`--snapshot-dir` wskazuje sam snapshot, nie nadrzędny pakiet fixture.
`--generated-root` domyślnie ma `data/generated`; również jawny root musi
kończyć się `data/generated`. Używaj rzeczywistej ścieżki bez symlinków
(np. `/private/tmp`, nie `/tmp` na macOS). Root nie może zawierać wejściowego
snapshotu ani znajdować się w jego wnętrzu. Wyjściem CLI jest JSON;
odrzucenie wejścia daje exit 2 i kod błędu bez zawartości wierszy.

```text
data/generated/snapshots/<source_dataset_id>/
  import_manifest.json           # importer version, code/lock/contract SHA, wyniki
  import_manifest.sha256
  snapshot/                      # wszystkie oryginalne bajty i source provenance
    snapshot_manifest.json
    manifest.sha256
    facts/...
    schemas/...
    reports/...
    manifests/dataset_manifest.v2.json
```

Bieżący fixture ma 25 tabel / 31171 wierszy / 74 pliki snapshotu.
Importer obsługuje też inne IDs, counts i partition layouts zgodne z kontraktem;
nie porównuje ich do oczekiwań jednego fixture. Wire `dataset.v1` nadal ma
odrębną rolę i algorytm identity; nie zastępuje source snapshotu.

## Weryfikacja i niezmienność

Wejście ma dokładny inventory: brakujące/dodatkowe pliki lub katalogi,
duplikaty references, symlink, special file, absolute/traversal paths i
nieobsługiwane wersje są błędem. Odczyt używa `O_NOFOLLOW` dla plików
i wszystkich komponentów ścieżki. Weryfikowane są source/snapshot descriptors,
provenance fingerprints, source manifest copy, schematy, reports i lineage.

Arrow schema obejmuje kolejność pól, typy, nullability i metadata.
Kontrola odczytanych wartości sprawdza count, grain uniqueness, non-null pola,
field/date ranges i każdą date partition. `order_items` używa jawnego mappingu
do daty źródłowego order. Hash wierszy respektuje UTC, NFC, decimal precision 28
i multiset z duplikatami. Inny podział plików nie zmienia logical ID.
Zmiana danych nie przechodzi po samym poprawieniu fizycznego checksumu.

Przed walidacją publikacji wejście zostaje skopiowane do prywatnego stagingu.
Kopia musi zgadzać się z odczytanym manifestem i każdym oczekiwanym SHA/size.
Wszystkie kontrole typed rows i gates pracują na tej kopii. Source/exporter
provenance nie jest przepisywane. Receipt przypina kod **konsumenta** i jego lock,
a snapshot nadal opisuje oryginalnego producenta.

Publikacja wykonuje fsync i atomowy rename **bez zastąpienia destination**:
Linux `renameat2(RENAME_NOREPLACE)`, macOS `renameatx_np(RENAME_EXCL)`.
Nie ma fallback do nadpisującego rename. Po wyścigu drugi importer ponownie
weryfikuje gotową publikację. Identyczny source/snapshot daje `reused` i zachowuje
wszystkie istniejące bajty, również receipt. Poprawne alternatywne execution
metadata lub partition layout tego samego logical snapshotu również go reużywa.
Inny snapshot pod tym samym source ID jest konfliktem. Uszkodzony, pusty lub
niekompletny destination nie jest nadpisywany.

Obsłużony błąd sprząta własny staging. SIGKILL może pozostawić prywatny katalog
`.snapshot-import-*` o mode 0700; nie jest on gotowym snapshotem. Retry tworzy
nowy staging i działa niezależnie. Taki stary katalog można usunąć po sprawdzeniu,
że jego proces już nie działa; nie usuwaj aktywnego stagingu ani gotowych IDs.

## Gotowość, historia i truth

Raport source policy 1.1.0 musi mieć dokładnie 46 znanych hard gates:
unikalne IDs, `passed`, threshold/value 0. Sprawdzane są fingerprint i spójność
readiness. Importer **nie uruchamia ponownie generatorowych walidatorów** ani
nie potwierdza autentyczności autora. Przyjmuje raport kwalifikowanego producenta;
integrity hash nie jest podpisem. Samodzielnie sprawdza transport i typed dane.

Domyślnie wymagany jest `forecast_source`. Możesz powtarzać `--require-use-case`;
każda wymagana bramka musi być `ready`. Obecne źródło ma forecasting/anomaly/
stockout/replay `not_ready`, rag `not_applicable`, `inventory_ready=false`.
Niegotowy stockout blokuje import wymagający stockout. To nie kwalifikuje modelu.

Wersje `daily_demand_versions`, null/zero, availability, mikrosekundy, currency
i pełny return tail są kopiowane bez collapse, normalizacji do latest lub joinów.
Watermark pozostaje deklaracją źródła, nie maksimum czasu odczytanych zdarzeń.
Odczyt as-of i mapping curated mają własny odbiór w 03.5/03.6.

Truth snapshot jest domyślnie odrzucany. Tylko jawne
`--allow-evaluation-truth` przyjmuje cztery znane tabele pod `evaluation_truth/`.
Zachowują klasę simulation truth, mode 0700 dla katalogów i 0600 dla plików.
Nie trafiają do facts, API, feature buildera ani automatycznych joinów.
Te uprawnienia chronią między użytkownikami systemu; nie izolują procesów tego
samego UID. Users, inventory i źródłowe operational AI outputs są poza allowlistą.

## Limity i powtórzenie kontroli

Domyślne limity: 2 GiB snapshotu, 10000 plików, 20 mln wierszy;
JSON metadata do 4 MiB, batch 8192 (max 65536), row group do 128 MiB,
odczytany batch do 64 MiB i canonical record do 64 KiB. CLI podaje
`--max-bytes`, `--max-files`, `--max-rows`, `--batch-rows`.
Parquet reader ogranicza też thrift metadata i wyłącza extension types.
Sort hashy i grain index używają lokalnego SQLite z cache 2 MiB.

```bash
make bootstrap check
make snapshot-import-check
uv run --locked --extra snapshot pytest -q tests/test_source_snapshot_import.py
uv run --locked --extra snapshot python scripts/check_snapshot_import.py \
  --snapshot-dir /rzeczywisty/eksport --output reports/import-acceptance.json
```

Check uruchamia dwa świeże procesy: import → reimport → verify, niezmienność
wejścia/publikacji oraz limit **300 s / 1024 MiB** na przebieg smoke.
[Evidence 03.4](evidence/03-04-importer.md) opisuje oba profile, truth i wheel.
Wyniki smoke nie kwalifikują ai-dev/ai-training end-to-end. Kolejny zakres to
**03.5 curated**, następnie 03.6; 04/06 jeszcze nie są otwarte.

Reader korzysta z [ParquetFile.iter_batches](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetFile.html#pyarrow.parquet.ParquetFile.iter_batches).
Linuxowa gwarancja odmowy nadpisania jest opisana w
[rename(2)](https://man7.org/linux/man-pages/man2/rename.2.html).
