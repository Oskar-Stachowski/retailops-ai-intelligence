# Wersje obserwacji i moment potwierdzenia kompletności

AI 09.10 dodaje `open_forecast_source_versions` jako przygotowanie do eksportu
etykiet. Czytnik działa wewnątrz jednej prywatnej kopii i jednego pełnego
replay źródła z AI 09.9. Rezerwuje pięć ról development przed pierwszym odczytem
parentów. Nie inicjalizuje ani nie rejestruje planu samodzielnie.

Snapshot 1.0 ma flagi `source_data_complete` oraz `quality_status` w
`daily_demand_observations`, ale nie w `daily_demand_versions`. Kontrakt historii
`observed-quantity-history-1.0.0` przechowuje ilość, status i availability;
nie potwierdza jakości każdej wcześniejszej wersji.

Czytnik łączy wszystkie obserwacje ze wszystkimi wersjami. Sprawdza klucz,
`observation_id`, politykę historii, ciągłość wersji, rosnące availability,
zamknięcie dnia o następnej północy UTC oraz zgodność najnowszej wersji
z bieżącą obserwacją: ilość, status i surowe `available_at`. Nie usuwa braków,
nie zamienia ich na zero i nie pomija wcześniejszych lub późnych wersji.
Osierocona historia, obserwacja bez historii i więcej niż osiem wersji
kończą cały odczyt błędem przed udostępnieniem pierwszego wiersza.

Wyłącznie dokładnie dopasowana najnowsza wersja otrzymuje dowód flag bieżącej
obserwacji. Dowód staje się znany w późniejszym z dwóch momentów
`curated_available_at`: wersji i obserwacji. Wcześniejsze wersje zachowują
`historical_quality_not_established`, nawet gdy ich ilość jest identyczna
z ostatnią. Nie odziedziczą kompletności przy późniejszym cutoff.

`ForecastSourceVersion.at_cutoff(cutoff)` zwraca `DemandVersion` z oryginalnym
momentem dostępności ilości. Przed dostępnością dowodu ustawia kompletność
na `false` i jakość na `incomplete`; po niej przekazuje jawne flagi źródła.
Nie przesuwa dostępności ilości do czasu dowodu i nie wybiera starszej,
kompletnej wersji. Dobór najnowszej wersji znanej na cutoff, dojrzałość,
zamknięcie miejsca i eligibility pozostają zadaniem czytnika etykiet 09.8.

Przykład: wersja 1 jest dostępna 3 lipca, korekta wersji 2 — 5 lipca,
a dowód jakości wersji 2 — 7 lipca. Na cutoff 6 lipca wersja 2 pozostaje
najnowszą znaną ilością, ale jej etykieta jest censored. Na cutoff 7 lipca
można użyć jawnych flag wersji 2. Wersja 1 nadal nie ma dowodu jakości.

Prywatny indeks SQLite używa cache 4 MiB, stron 4096 B i limitu 128 MiB.
Odczyt Parquet odbywa się partiami po 256; w pamięci pozostaje najwyżej osiem
wersji jednej obserwacji. Każdy wiersz indeksu jest związany z kluczem i SHA-256.
Indeks jest query-only, sprawdzany przed iteracją, w czasie niej i przy wyjściu.
Iterator użyty poza kontekstem odrzuca dostęp przed pobraniem następnego wiersza.
Prywatne pliki mają tryb 0600, katalogi 0700 i są sprzątane przy zwykłym
wyjściu oraz wyjątku. SIGKILL nadal może zostawić scratch i nierozliczony odczyt.

`reader.receipt()` jest dostępny dopiero po udanym wyjściu z kontekstu:
po kontroli indeksu, oryginalnych i prywatnych parentów, runtime oraz zapisaniu
wszystkich zakończeń audytu. Błąd zapisu zakończenia nie publikuje receipt;
rezerwacje pozostają naliczone. Receipt ma counts i hash kompletnego inwentarza,
bez ilości targetów. CLI `python -m
retailops_ai.evaluation_campaign.source_versions_cli` przyjmuje te same
argumenty przypiętego protokołu i dziennika co CLI source replay 09.9.

Kontrakty są w `contracts/evaluation/v8`; v5–v7 oraz historyczne receipty
pozostają bez zmian. Nowy runtime wymaga nowego planu w istniejącym dzienniku,
z zachowaniem historii i budżetu. Receipt jest diagnostyczny i nie upoważnia
do ponownego odczytu bez nowej rezerwacji oraz replay.

To nadal nie jest eksport `outcomes.jsonl` związany z fizycznymi kluczami
pięciu ról. Nie dowodzi prawdy producenta ani niezależności statystycznej.
Pełny parent obejmuje również późne, purged i pozarolowe obserwacje; taki
dostęp jest rejestrowany jako szeroka ekspozycja, a nie jako odczyt jednego
wydzielonego zbioru. AI 09 pozostaje `not_ready`, independent evaluation,
final test i promotion są zamknięte.

[Dowody i ograniczenia odbioru](evidence/09-10-forecast-source-versions.md)
rozróżniają testy komponentu, kontrolę pakietu oraz próby zasobowe.
