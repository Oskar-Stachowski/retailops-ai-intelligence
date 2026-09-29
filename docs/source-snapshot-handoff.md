# Handoff źródła RetailOps — AI 03.3

Wspólny kontrakt `retailops-source-snapshot-handoff` **1.0.0** przyjmuje snapshot
**1.0.0**, source **2.6.0**, format `retailops-parquet-1.0.0` i source multiset
canonicalization **1.6.0**. To osobna rodzina wejścia; nie zastępuj jej istniejącym
wire `dataset.v1` ani jego algorytmem logical_rows_sha256.

Samowystarczalny [fixture](../data/fixtures/ai-smoke-v1) zawiera pełny eksport
25 tabel, 31171 wierszy i 74 pliki snapshotu: Parquet facts, schemas, reports
oraz pełną source lineage. Seed 42, 20 produktów, 3 stores, 2 warehouses,
30 dni 2026-07-02–2026-07-31; return tail i availability zachowują dalsze daty.
Pakiet ma 2048665 B. Nie zawiera generatora, users, inventory, wyników AI ani truth.

[Registry](../contracts/source_snapshot/v1) ma reviewowaną kopię kontraktu,
expected manifest i kompletny schema manifestu z lokalnymi `$defs`.
Fixture jest identyczny z upstream; zmiana wymaga obu repo i nowych oczekiwań.
Źródło/exporter provenance jest zamrożone na RetailOps `6561481` i nie jest
przepisywane podczas handoff. IDs i daty per tabela/pole podaje expected manifest.

## Weryfikacja bez RetailOps

```bash
make bootstrap handoff-check
uv run --locked python scripts/check_snapshot_handoff.py
uv run --locked pytest -q tests/test_snapshot_handoff.py
```

Checker weryfikuje wyłącznie reviewowany v1 fixture: wersje, schema, identity,
lineage, fact allowlistę, physical inventory/SHA/bytes i gotowość źródła.
Nie wymaga PyArrow, generatora, działającej DB, brokera ani AWS.
Test kopiuje tylko registry, pakiet i checker do odłączonego katalogu,
uruchamia `python -I` dwukrotnie i zachowuje wejściowe bajty.
`make check`/Required CI obejmują ten checker i testy negatywne.

To **odbiór kontraktu i transportu**, nie typed import dowolnego snapshotu.
[03.4 dostarcza importer](source-snapshot-import.md): odczyt Parquet,
recomputed canonical hashes, idempotentną atomową publikację i konflikty
w `data/generated/snapshots/`.
[03.5 curated](curated.md) dostarcza mapping, kwarantannę i historyczny as-of;
03.6 sprawdzi oba smoke dwukrotnie end-to-end.

## Zasady dla importera i curated

- `contract.json` podaje dokładne typy/nullability, grain i klasy 25 facts
  oraz czterech opcjonalnych evaluation truth tabel. Truth nie jest domyślnym
  inputem runtime/feature buildera; bieżący fixture jest facts-only.
- Product ID jest wspólny z catalog. Selling location pochodzi z jawnych
  channel assignments legacy store/channel; stock location z fulfillment route.
  Wybieraj validity i wersję znaną w origin, bez losowego uzupełnienia mappingu.
- Quantity jest liczbą saleable packs, pack_quantity/pack_unit opisują zawartość;
  currency pozostaje źródłowe. Money ma decimal128(38,2), rates decimal256(76,40).
- Business date ma UTC; lokalna Warsaw/Berlin to metadata kalendarza.
  Timestamp ma UTC i mikrosekundy. `available_at <= origin` ogranicza wiedzę.
  Zachowuj wszystkie daily_demand_versions; late correction nie zmienia starego
  origin. Null/missing nie jest zero, closed nie oznacza gotowości scoringu.
- Source canonical hash używa sorted multiset canonical JSON rows z LF;
  zachowuje duplikaty. Decimal normalize ma precision 28; timestamp używa
  UTC ISO z `+00:00`. Pełne reguły podaje kontrakt; foundation unique-key hash
  nie jest zamiennikiem. Physical SHA i logical ID mają różne role.
- Watermarks nie są maksimum czasu zdarzeń. Nie obcinaj 39-dniowego return tailu
  do end date i nie traktuj label maturity jak availability cechy.
- Gotowy jest forecast_source, modele/anomaly/stockout/replay not_ready,
  rag not_applicable, inventory_ready=false. Żadne inventory features ani labels
  nie są kwalifikowane tym pakietem. Odrzucaj wymagane niegotowe use cases.

Zmiany producer/consumer mają osobne commity i wspólną wersję. Obecny branch
importera jest niezależny od równoległego AI 12. [Evidence](evidence/03-03-handoff.md)
podaje rewizje, kontrole i granice; 04/06 czekają na 03.6.
