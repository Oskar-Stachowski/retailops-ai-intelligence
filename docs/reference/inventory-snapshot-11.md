# Inventory snapshot 1.1 — AI 06.6b.2c.1

Ta ścieżka importuje niezmienny source 2.7 i qualification 1.0 z RetailOps.
Kontrakt 1.0/source 2.6 zachowuje własny czytnik. To lokalny odbiór transportu
i uzgodnień inventory; curated 1.1, pełna publikacja 03 i modele są kolejnym zakresem.

## Import

```sh
uv sync --locked --extra snapshot
uv run retailops-ai-snapshot import \
  --snapshot-dir /path/to/source-sha256-ID \
  --generated-root data/generated \
  --require-use-case inventory_source
uv run retailops-ai-snapshot verify-import \
  --import-dir data/generated/snapshots/source-sha256-ID
```

Worker facts mają dokładną allowlistę **43 tabel**: 25 dotychczasowych tabel
commerce oraz 18 dodatkowych tabel native inventory; `return_events` jest wspólne.
Nie zawierają users, operacyjnych wyników modeli, legacy price/promotion/return
adapters ani private simulation fields. Int64, booleany, nullable fields,
UTC microseconds, dates i fixed-scale money zachowują typy Arrow.

Importer niezależnie sprawdza byte hashes, reviewed JSON schemas, typed multiset
hashes, unikalny grain, ranges, dokładną allowlistę 36 bramek producenta,
source → qualification → snapshot lineage oraz source projection hashes.
Native CSV identity jest odtwarzana z typowanego Parquetu i sortowana na dysku.
Commerce zachowuje swoją wcześniejszą typed identity. Importer nie regeneruje
basketów ani nie zależy od checkoutu lub modułów Pythona producenta.

Następnie uzgadnia opening/scope/sign/chronology/balance/transfer pairs,
known daily snapshots z ledgerem, historyczny routing sprzedaży, inventory
issues, ilości i ceny commerce, refundy/restock, quotes/orders/plans/receipts.
Private SQLite indexes i staging są usuwane po odczycie. Source report jest
sprawdzany jako dowód producenta; importer nie wylicza ponownie całych 36 bramek
symulatora, prywatnego popytu ani reorder policy.

## Evaluation truth

Wariant private wymaga `--allow-evaluation-truth` przy imporcie i każdym
późniejszym odczycie. Zawiera 12 osobnych tabel w `evaluation_truth` oraz
`evaluation_truth/qualification/{qualification_manifest.json,qualification_report.json,simulation_truth/inventory_qualified_windows.json}`.
Ich checksums, lineage, schema, grain/status i raport agregacji są weryfikowane.
Producer rekwalifikuje okna przy eksporcie; importer nie wylicza ponownie
pełnej fizycznej i lifecycle kwalifikacji labeli. Żadne z tych plików nie jest worker fact.

Publikacja jest atomowa i nie nadpisuje istniejącego source ID. Powtórny import
tych samych bajtów zwraca `reused`; inny wariant snapshotu tego samego source
wymaga osobnego generated root. Receipt przypina kod importera, lock i właściwy
handoff contract. Ten sam importer nadal odczytuje snapshot 1.0.

Odczyty są ograniczone do 2 GiB, 10 000 plików i 20 mln wierszy, z batchami
do 65 536 wierszy/64 MiB i row groups do 128 MiB. JSON metadata ma limit 4 MiB.
To limity odczytu; odbiór smoke mierzy rzeczywisty czas i RSS osobno.

## Co pozostaje

`build_curated` odrzuca snapshot 1.1 kodem
`inventory_curated_contract_not_yet_supported`. Kolejny zakres rozszerzy curated
o native grain, historyczny mapping i causal availability oraz odczyty as-of.
Dopiero pełny source → qualification → snapshot → import → curated wraz
z odbiorem budget/truth isolation pozwoli przełączyć domyślne źródło AI.
`source_ready`, `inventory_ready`, `model_ready` pozostają false w frozen source.
Oceny 04/05 wymagają ponowienia na nowych IDs; model 08 wymaga własnego odbioru.
