# AI 08.7 — odbiór odczytu faktów z dysku

Nowa ścieżka `stockout_storage` strumieniuje publiczny curated do prywatnej
bazy SQLite i przechowuje cache jednej fizycznej serii. Zapis cech ma
schema 2.1 i własne ID; kod i artefakty v1/v2.0 pozostają zachowane.
[Kontrakt](../reference/stockout-disk-facts.md) opisuje granice składników.
Cały AI 08 pozostaje **not ready**; większego profilu nie uruchomiono.

## Próbka i zgodność

Użyto istniejącej próbki `ai-temporal-smoke`, seed 42, 102 dni, 8 produktów
i 2 fizycznych lokalizacji, producent `08639e9188badb352ed64686a088fe237badad41`.
Nie regenerowano źródła. Odbiór odczytuje rzeczywisty Parquet, rozwiązuje
referencje i porównuje wszystkie pola, status, historię oraz lineage.

| Kontrola | Wynik |
|---|---:|
| Wszystkie punkty | 1 632, identyczne z v1 |
| Eligible / istniejący brak / brak danych | 1 104 / 432 / 96 |
| Pliki Parquet | identyczne bajty z v2.0 |
| Części / wszystkie pliki | 16 / 49 |
| Bundle 2.1 z manifestem | 1 553 662 B |
| Wcześniejsze modele i wyniki development | sześć wariantów, identyczne |
| Pliki wejściowe i sześć rodziców v1 | 573 pliki / 63 417 718 B, bez zmian |

Logiczna suma punktów nadal wynosi
`4a55767d14681273301dbe910262cac93b8a685d0aaeba45f4de91a167ce8b40`.
Zachowany development ID to
`development-sha256-18e71a00d92d1f5b22af4df603e01d8084155e61aa4e30102402db0d25a6f462`.
Wybór na tune pozostaje provisional `hist_gradient_boosting:without_upstream`.
Odtworzenie modeli obejmuje również pełne stare CLI verify rodziców,
z prywatną kwalifikacją etykiet; nie obejmuje oceny outcomes final testu.

## Pomiar świeżych procesów

Każda operacja działała w nowym procesie. Zewnętrzny sampler psutil
mierzył jednoczesną sumę RSS jego drzewa co 50 ms; sampler nie należy
do tego drzewa. Operacja obejmuje pełny replay curated, pieczętowanie
faktów, indeks i przygotowanie lub sprawdzenie cech. Timer operacji
nie obejmuje startu Pythona; receipt podaje również wall ze startem.
Pełne CI rozpoczęto dopiero po zakończeniu tych pomiarów. Aktywności
pozostałych sesji komputera nie kontrolowano.

| Operacja | Wall operacji | CPU operacji | Próbkowany peak RSS drzewa |
|---|---:|---:|---:|
| Build v2.0 | 18,748 s | 18,567 s | 175,0 MiB |
| Build 2.1 z dysku | 15,438 s | 15,357 s | 131,5 MiB |
| Rebuild 2.1, reuse | 15,376 s | 15,301 s | 131,6 MiB |
| Verify 2.1 | 15,313 s | 15,259 s | 131,8 MiB |
| Verify v2.0 | 18,340 s | 18,279 s | 174,6 MiB |

Build zużył około 25% mniej peak RSS i trwał około 18% krócej niż v2.0
w tym pomiarze. To porównanie dwóch świeżych procesów na tej samej małej
próbce, a nie prognoza większego profilu. Nie obejmuje regeneracji,
importu, etykiet, upstream, treningu ani budżetu całego pipeline.
Osobny proces porównujący punkty i modele używał dawnych pełnych list;
jego pamięci nie doliczamy do pomiaru przygotowania.

Magazyn miał 13 942 wiersze / 15 923 617 B kanonicznego payload.
Główna baza zajęła 25 575 424 B i została usunięta po kontekście.
Największa wybrana seria miała 2489 wierszy / 2 782 285 B payload.
Limity 128 MiB bazy, 8 MiB cache, 20 000 wierszy / 16 MiB selekcji
nie stanowią limitu całego scratch/RSS.

## Pakiet i kontrole

Odłączony zainstalowany wheel wykonał build/rebuild/verify w
18,030 / 18,330 / 19,120 s przy równoległym CI. Odtworzył identyczne
manifesty, ID i bajty wszystkich plików; 29 modułów konsumenta pochodziło
z zainstalowanego pakietu, producent nie był importowalny. Wszystkie
pliki wyjścia mają 0600; wejścia i sześć rodziców pozostały bez zmian.

30 nowych przypadków obejmuje natywne SQL i cache przy odwróconej
kolejności origin, granice wiedzy ±1 µs, stare wersje, fakty przyszłe,
pełny roundtrip, stary replay v2.0 oraz wersjonowanie 2.1. Testy kontrolują
limity bazy/selekcji, plan SQL bez pełnego sortowania, zmiany payload
i indeksu, zmianę wejścia między verify a odczytem i zmianę tabel
nieużywanych do cech. Przerwany zapis usuwa staging; retry jest pełny,
konflikt nie nadpisuje celu. Braki, dodatki, podwójne części, ponownie
zahashowane uszkodzenia i symlinki są odrzucane.

Testy cech/partycji/magazynu mają 82/82 zaliczeń w 51,20 s bez ostrzeżeń;
split/trening 31/31 w 3,64 s. Jeden warning w drugim przebiegu dotyczył
sandboxowego odczytu liczby fizycznych CPU przez joblib. Łącznie 113/113
testów i 30 nowych. Pełne `make ci-local` zakończyło się z exit 0:
**1913/1913 testów w 1953.31 s**, bez ostrzeżeń, Ruff/format
589 plików, mypy 353 modułów, wszystkie 21 targetów `check`, pakiet,
Compose config i oba skany sekretów. Końcowa aktualizacja samych dowodów
ma ponowne kontrole dokumentacji i sekretów; testowany kod pozostał
niezmieniony. [Receipt JSON](08-07-stockout-disk-facts.json)
wiąże wyniki, pomiary, policy, identyfikatory i fingerprint wejść.
Wcześniejszy commit `ebe1f45` ma zielone Required CI PR i push;
nowy commit wymaga własnego CI w draft PR #14.

## Zakres pozostający

Ledger i ruchome okna nadal liczy projekcja v1. Etykiety, upstream,
split i trening wymagają partycjonowania oraz ograniczonego czytnika.
Przed większą generacją trzeba zamrozić rzeczywisty profil i budżet,
sprawdzić zasoby i odebrać cały pipeline. Potem pozostają niezależna
ocena, progi oraz registry/batch/read API. Final test nie jest oceniony,
progi niezatwierdzone i model niepromowany; AI 05/v12 nie zmieniono.
