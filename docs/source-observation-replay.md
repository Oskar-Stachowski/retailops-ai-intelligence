# AI10 — odbiorca replay historii obserwacji

Pakiet `retailops_ai.source_replay` oblicza ograniczony kandydat historii
`daily_demand_versions`. Korekta zastępuje wkład poprzedniej wersji dla odczytu
as-of; obydwie wersje pozostają w historii. Odbiorca porównuje nakładające się
zdarzenia z receiptami objętymi capture, wykrywa luki i kolizje identyfikatorów.

**Status: mechanika odbiorcy, protokół proponowany.** Nie istnieje jeszcze
źródłowy producent tego capture ani topicu `retailops.source-observations.v1`.
Nie jest to odbiór live SQL, PostgreSQL/brokera, trwałego checkpointu, ACK/DLQ
ani pełnego 43-table SourceSnapshot 1.2. Warstwa nie wykonuje ACK, nie zapisuje
do bazy i nie uruchamia modeli. Dotychczasowy bundle zachowuje
`replay_handoff=false`, a REST nadal zwraca unsupported dla snapshot/handoff.
Fixture transportu i dodatkowej korekty są jawne.

## Kontrakt

Nie zmieniamy SourceSnapshot, frozen v12, curation ani locka zależności.
Nowe [schematy](../contracts/source_replay/v1/capture.schema.json) obejmują tylko
jedną tabelę. Pola faktu odpowiadają natywnemu `daily_demand_versions`:
`id`, `observation_id`, `business_date`, `product_id`, `selling_location_id`,
`channel`, `version`, `observed_units`, `observation_status`, `available_at`,
`history_policy_version`. Polityka to `observed-quantity-history-1.0.0`.
Grain biznesowy to data–produkt–selling location–kanał; historia dodaje wersję.

Odrębny `source_authority_id` identyfikuje właściciela strumienia. Nie używamy
zmiennego zbioru jako niezmiennego `source_dataset_id`. Capture wiąże authority,
cluster ID, natywny topic ID, nazwę topicu, wszystkie partycje i ich `next_offset`,
posortowane wersje faktów oraz wszystkie semantyczne receipts od offsetu zero.
Hash całego dokumentu wyznacza `observation-capture-sha256-*`.
To tożsamość treści, a nie dowód autoryzacji producenta lub izolacji SQL.
Nie wolno dorobić wektora do istniejącego bundle po jego pobraniu.

`event_id` i hash semantycznego envelope są oddzielne od wersji biznesowego
faktu. Nowy envelope dla tej samej wersji tworzy receipt i przesuwa granicę,
ale nie dodaje drugiego wkładu. Ten sam offset musi wskazywać dokładnie ten sam
envelope i fakt. Ponowne użycie event ID z inną treścią, row ID z inną wersją,
wersji z inną wartością lub naturalnego grain z innym observation ID zatrzymuje
przetwarzanie. Receipts nie są fingerprintem surowych bajtów Kafka; przyszła
warstwa trwałości musi zachować także raw/key/headers i transport metadata.

## Krok po kroku

1. Utwórz `Stream` z zaufanych, wcześniej zweryfikowanych danych producenta.
   Odbiorca wymaga 1–32 jawnych partycji `0..N-1`, również pustych. Zmiana
   authority, cluster lub topic ID wymaga resync, nie kontynuacji starego stanu.
2. Dla pełnego przebiegu utwórz `ObservationHistory(stream, partitions=N)`.
   Nowy stan zaczyna na zero. Nie inicjalizuj go od aktualnego broker high
   watermark, jeśli nie ma wiarygodnego capture z pokryciem poprzednich faktów.
3. Przekazuj typowane `Record` do `process(record, stream=stream)` w kolejności
   offsetów każdej partycji. Partycje mogą się przeplatać. Offset większy od
   oczekiwanego przerywa odbiór; uszkodzony rekord nie ma ścieżki skip/ACK.
4. Odróżnij transportową kolejność od wersji faktu. Odbiorca przechowuje korekty
   dostarczone przed poprzednikami. Odczyt i capture są zablokowane, dopóki
   historie nie zawierają wersji `1..max` i niemalejącej dostępności.
5. Użyj `apply_batch(records, stream=stream)`, gdy potrzebujesz osobnego
   kandydata. Metoda zwraca nowy obiekt; dowolny błąd pozostawia wejście bez zmian.
   Batch ma także limit 40 000 pozycji, wliczając powtarzany overlap.
   To atomowość obliczenia w pamięci, nie transakcja PostgreSQL.
6. `capture()` wyznacza zamknięty, niezmienny dokument. `canonical(capture)`
   daje jego bajty. Ograniczenia to 10 000 wersji, 20 000 receipts i 16 MiB
   dokumentu. Dalsze przetwarzanie wymaga nowego rozwiązania capture/compaction;
   nie wolno usuwać receipts dla odciążenia tego ograniczonego protokołu.
7. Wznów przez `ObservationHistory.restore(raw, stream=stream, partitions=N)`.
   `N` musi pochodzić z zaufanej topologii, a nie z pobranego capture; usunięcie
   pustej partycji też wymaga odmowy i resync. Walidator
   sprawdza hash, kompletny wektor, ciągłość offsetów, związanie każdego receipt
   z faktem/envelope, unikalność grain/wersji i kompletną historię. Doklejony
   fakt lub ponownie zahashowany dokument z luką jest odrzucony. JSON z podwójnymi
   kluczami, nieznanymi polami lub niewłaściwym typem także jest odrzucony.
8. Pierwsza pozycja nieobjęta capture to `boundaries[*].next_offset`.
   Bezpieczny overlap jest dopuszczalny tylko tam, gdzie zgadza się receipt.
   Ten odbiorca nie potrafi wznowić historii usuniętej przez retention/compaction.
9. `as_of(origin)` wymaga UTC i wybiera najwyższą wersję z `available_at <= origin`
   oraz `business_date <= origin.date()`. Przyszła korekta nie przecieka do
   wcześniejszego odczytu. `closed`, `observed_zero` i `missing` pozostają różne.
   `quantity_total` odrzuca nieznaną ilość; nie zamienia jej na zero.
10. Przed podłączeniem do modeli wykonaj pełną curation wszystkich zależności:
    mapping, plany/routing, jakość i ich dostępność. Odbiorca jednej tabeli nie
    zastępuje `CuratedReader` ani całego source dataset i nie aktywuje modelu.

## Odbiór

```sh
make bootstrap
make observation-replay-check
make integration-replay-test
```

Pierwsza bramka weryfikuje i importuje istniejący niezmienny snapshot ai-smoke,
buduje rzeczywistą curation oraz czyta 1612 natywnych wersji. Fixture envelopes
rozdziela na trzy partycje. Porównuje pełny przebieg z capture 806 wersji bazowych
i replay obejmującym overlap, duplicate z nowym envelope oraz późną korektę.
Porównuje trzy odczyty as-of z zamrożonym `CuratedReader`. Raport zawiera źródłowe
IDs, dokładny wektor, hashe capture i sumy przed/po korekcie. To fixture
30-dniowe, bez kwalifikacji ML. CI publikuje raport jako `ai10-observation-replay`.

Testy negatywne sprawdzają kolizje, lukę partycji, niekompletną historię,
regresję dostępności, zmianę strumienia, obce authority, przyszłą dostępność,
missing/closed, granice rozmiaru oraz zmodyfikowany i ponownie zahashowany capture.

## Następny krok i rollback

Source musi utrwalić capture, wersje i granice w jednej spójnej transakcji,
z niezależną autoryzacją dostępu. Potem potrzebna jest trwała projekcja AI:
fact inbox, raw receipts, checkpoint i quarantine z fencing w jednej transakcji,
ACK dopiero po commit, oraz testy realnego SQL/brokera, SIGKILL i konkurencyjnego
eksportu. Pełny handoff musi objąć wszystkie wymagane tabele i zależności,
a temporalny odbiór modeli użyć profilu 102 dni.

Zmiana jest opt-in biblioteką i bramką offline. Rollback polega na niewłączeniu
odbiorcy lub wycofaniu przyrostu. Nie ma migracji, konfiguracji runtime,
przełączenia produkcyjnych modeli ani zmian w istniejących capabilities.
