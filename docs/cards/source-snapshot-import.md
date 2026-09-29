# Karta danych — imported source snapshot

Rola: **source snapshot**, importer receipt **1.0.0**, source schema **2.6.0**,
handoff/snapshot **1.0.0**. Właściciel źródła: RetailOps; konsument:
RetailOps AI Intelligence. Import nie tworzy nowego curated/feature/label ID.
Source/snapshot IDs i provenance pozostają źródłowe.

Referencyjny input: [ai-smoke-v1](../../data/fixtures/ai-smoke-v1/snapshot),
25 tabel / 31171 wierszy, 20 produktów, 3 sklepy, 2 magazyny, seed 42,
history 2026-07-02–2026-07-31. Row counts, grain, typy i zakresy każdego pola
podaje [expected manifest](../../contracts/source_snapshot/v1/expected_manifest.json).
Date ranges obejmują również availability i return tail; nie obcina się ich do
history end. Watermarks deklarują kompletność poszczególnych strumieni.

Source ID: `source-sha256-a12866e1099c3ae2ae7c73cac5c533a35733618d85a3728cd5f0e1c7b527fc00`.
Snapshot ID: `snapshot-sha256-4d856185ebbe3a8f07dc468468c54eacf69aa40b7d49bfff7e39af8ddde815a4`.
Producent fixture: `6561481`. Importer: `aadd145`; receipt zapisuje exact
module hashes, consumer lock SHA, handoff SHA i PyArrow version.

Money jest decimal128(38,2), rates decimal256(76,40), quantity integer packs,
currency zachowane. UTC/business dates, null/zero/closed i wszystkie wersje
obserwacji są kopiowane bez zmian. Selling/stock location mapping pochodzi
z jawnych source assignments/routes, nie z dopasowania nazw.

Logiczna identity stosuje NFC/UTC multiset canonicalization 1.6.0, niezależną
od file layout. Każdy fizyczny plik ma osobny SHA/size; importer przelicza
typed hash, grain, count i ranges. Raport kwalifikacji pochodzi od producenta;
consumer sprawdza spójność/gates, nie wykonuje pełnej kwalifikacji source od nowa.

Gotowy jest **forecast_source**; modele/anomaly/stockout/replay `not_ready`,
rag `not_applicable`, inventory false. Nie są to realne transakcje klientów
ani dowód skuteczności modeli. Truth jest optional, domyślnie zabroniony;
opt-in zachowuje osobny namespace i nie daje automatycznego dostępu aplikacji.

Przechowywanie: `data/generated/snapshots/<source_id>/snapshot`, poza Git,
bez nadpisywania; receipt obok snapshotu. Staging nie jest gotowym ID.
Retencja/cleanup wymaga zachowania wykorzystanych pins i sprawdzenia aktywnych
procesów; importer nie usuwa istniejących snapshotów ani fixture.
[Runbook](../source-snapshot-import.md), [odbiór](../evidence/03-04-importer.md).
