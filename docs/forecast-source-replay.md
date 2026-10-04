# Audytowane odtworzenie pełnego źródła forecast

AI 09.9 sprawdza cały snapshot 1.0 i odtwarza całe curated z jego faktów.
Porównuje pełny dokument logiczny, wszystkie tabele, ilości, availability,
mapowania, lineage, kwarantannę, parametry i tożsamość transformacji. Sama
zgodność checksum curated nie jest wystarczającym dowodem. Cały AI 09
pozostaje `in_progress / not_ready`.

## Zakres odczytu i trwały budżet

`ForecastSourceReplayProtocol` przypina source/snapshot/curated, oba fizyczne
manifesty, pełne parametry źródła, seed danych oraz deklaracje pięciu ról
development. Przed pierwszym odczytem rodziców trzeba zarezerwować wszystkie
pięć bindingów `verification` w istniejącym
[dzienniku](outcome-access-journal.md). Operacja kosztuje pięć odczytów z
globalnego budżetu. Częściowa odmowa rezerwacji nie otwiera danych, ale
wcześniejsze rezerwacje pozostają naliczone jako failed. Powtórzenie całego
replay wymaga kolejnych pięciu rezerwacji; nie ma resetu po awarii.

W tej operacji `outcome_artifact_sha256` oznacza hash **całego manifestu
curated**, a nie pliku targetów jednej roli. Protokół jawnie deklaruje
ekspozycję wszystkich faktów i curated, w tym późniejszych wersji, dat
purged oraz dat poza oknami ról. Dziennik jest częściowym audytem
współpracujących czytników; nie potwierdza świeżości ani niezależności danych.

Deklaracje populacji są metadanymi. Ta kontrola nie czyta fizycznych feature
lub partition rows, nie sprawdza ich pokrycia i nie wybiera etykiety na cutoff.
Receipt ma `feature_and_partition_rows_verified=false` oraz
`scoped_outcome_evidence_verified=false`. Nie wolno używać go do podnoszenia
kwalifikacji istniejących [dowodów etykiet](forecast-outcome-reader.md).

## Kolejność weryfikacji

Po rezerwacjach czytnik sprawdza przypięte manifesty i dopuszczony inventory.
Snapshot z evaluation truth lub unlisted file jest odrzucany przed odczytem
zawartości takich plików. Hashowane i kopiowane są tylko pliki z dozwolonej
listy. Symlinki i pliki specjalne są odrzucane.

Oba rodzice trafiają do prywatnego katalogu 0700, z kopiami plików 0600. Istniejący
importer sprawdza pełne typed Parquet, schema, grain, ranges, raporty i source
hard gates. Istniejący verifier curated sprawdza wszystkie jego tabele.
Następnie przypięta bieżąca transformacja odtwarza całe curated z prywatnego
snapshotu; dowolna różnica logiczna powoduje odmowę. Inny podział Parquet
na części jest dopuszczalny, ponieważ nie zmienia logicznych danych.

Wszystkie pola dokumentu poza fizycznymi referencjami `files` muszą być
identyczne. Inna wersja implementacji transformacji wymaga osobnego odbioru;
nie jest przyjmowana jako równoważna na podstawie samego deklarowanego ID.
Oryginały, prywatne kopie i runtime są ponownie sprawdzane przed ukończeniem.
Prywatne dane oraz uchwyty istniejących SQLite są sprzątane również po błędzie
i przerwaniu. Receipt wraca dopiero po zakończeniu wszystkich pięciu wpisów
w dzienniku. SIGKILL może pozostawić charged/unresolved wpisy i scratch,
jak każdy proces zakończony bez wykonania cleanup.

## Użycie

Plan dostępu musi mieć dokładny hash protokołu, bieżący pełny runtime oraz
dokładnie bindingi zwrócone przez `replay_bindings(protocol)`. Czytnik nie
tworzy dziennika, nie rejestruje planu i nie przyjmuje wcześniejszego receipt
jako upoważnienia do kolejnego odczytu.

```python
receipt = verify_forecast_source_parent(
    snapshot_root,
    curated_root,
    protocol,
    journal=shared_journal,
    plan_sha256=frozen_plan_sha256,
)
```

```bash
python -m retailops_ai.evaluation_campaign.source_replay_cli \
  --snapshot /abs/snapshot --curated /abs/curated \
  --protocol /abs/source-replay-protocol.json \
  --expected-protocol-sha256 "$PROTOCOL_SHA256" \
  --journal /abs/shared-journal \
  --access-plan-sha256 "$ACCESS_PLAN_SHA256"
```

CLI wypisuje tylko typed receipt: liczniki, hashe, ID i granice kwalifikacji.
Exit 0 oznacza zgodny consumer replay, nadal `evaluation_status=not_ready`;
exit 2 oznacza odmowę. `independent_evaluation`, final test i promocja
pozostają niedopuszczone przez kontrakt.

## Zasoby i granica dowodu

Reuse obejmuje istniejący importer, curated transform, strumieniowe czytniki
Parquet oraz bounded hashing z AI 09.8. Rodzic ma maksymalnie 256 MiB i 4096
plików; pełny profil ma maksymalnie 100 000 wierszy na rodzica, a batch 256
wierszy. Nadmiar powoduje odmowę, bez obcinania danych. Limity wejść i batchy
nie są gwarancją całkowitego RSS ani scratch dla każdego dozwolonego profilu;
większe profile wymagają osobnego pomiaru całego procesu.

Kontrolny odbiór 25 tabel / 31 171 wierszy daje identyczny dokument native
i wheel (poza unikalnymi ID rezerwacji): peak RSS 108.36 MiB, próbkowany
logiczny scratch 36 873 750 B, czas około 10–11 s. RSS obejmuje całe własne
drzewo procesu; scratch jest sumą rozmiarów plików, nie zaalokowanych bloków.
Przygotowanie istniejącego fixture jest poza pomiarem. To profil pełnego
source replay, bez treningu; nie jest porównaniem kosztu całych etapów AI.

Przed tym przyrostem odczytano metadane AI 08.13 i jego rozwiązanie polegające
na pełnym replay w prywatnym kontekście. W AI 09 wykorzystujemy istniejące
mechanizmy forecast; adapter stockout ma inny grain i nie jest kopiowany.
Przejrzano również nowy receipt AI 08.14 z większego pilota całego pipeline;
jego limity i koszty dotyczą innego zakresu niż ten kontrolny replay.
Otwarte sesje AI 07–08 nie są modyfikowane. Pomiary kontrolne tego przyrostu
opisuje [odbiór](evidence/09-09-forecast-source-replay.md).

Consumer replay potwierdza zgodność curated z przypiętym snapshotem i
bieżącą transformacją. Nie jest niezależnym audytem prawdziwości producenta,
wynikiem modelu ani kwalifikacją source 1.1 / nowych upstream AI 07–08.
Pozostają audytowany eksport wersji obserwacji do pełnych kluczy pięciu ról,
sprawdzenie jakości i kompletności bez domyślnych wartości oraz powiązanie
etykiet, fitów i kandydatów z zamrożonym protokołem treningu.

W aktualnym kontrakcie snapshot 1.0 `daily_demand_observations` zawiera
`source_data_complete` i `quality_status`, ale `daily_demand_versions` nie
zawiera tych pól. Eksporter nie może wstawić domyślnie `true` i `valid`.
Musi kwalifikować ich źródło i semantykę czasową dla konkretnej wersji;
sam poprawny checksum i kompletność końcowej obserwacji nie dowodzą, że
wcześniejsza wersja była kompletna na cutoff. Gdy takiego dowodu brak,
pełny klucz pozostaje w coverage z censored label. Replay 09.9 celowo nie
wydaje w tej sprawie decyzji eligibility i nie zmienia deklaracji czytnika.
