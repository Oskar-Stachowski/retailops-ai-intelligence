# AI 08.2 — cechy PIT i temporalne członkostwo

Przyrost buduje cechy tylko z wiedzy dostępnej w origin oraz wyznacza
cztery okresy z wykluczeniem okien nachodzących na następny okres.
[Kontrakt i polecenia](../reference/stockout-features.md) opisują reguły;
[wersjonowany pomiar](08-02-stockout-features.json) zawiera polityki,
identyfikatory, hashe kodu, wejść i wyników. Baza AI 05 to
`f4ae14fe6588b91506383a709b5a0185208a4ea6`, poprzedni przyrost etykiet
`172496bad2eace2e94485be495a2010e411af6a2`.

## Natywna próbka 102 dni

W osobnym worktree producenta na `08639e9188badb352ed64686a088fe237badad41`
wykonano profil `ai-temporal-smoke`, seed 42, 2026-04-21–2026-07-31:
8 produktów, 2 fizyczne magazyny, 1632 dobowe origin. Natywna generacja,
kwalifikacja inventory i oba snapshoty trwały 90,77 s. Jest to jawna
ograniczona próba przygotowania, bez oceny modeli i bez operacyjnej bazy.

| Status cech | Punkty |
|---|---:|
| `eligible` | 1104 |
| `already_stockout` | 432 |
| `insufficient_data` | 96 |

96 wyłączeń obejmuje 68 punktów ze zbyt krótką kompletną historią sprzedaży
i 28 punktów nieaktywnego produktu. Cechy mają 10 249 555 bajtów, około
9,77 MiB, i mieszczą się w jawnym limicie 16 MiB. Pierwszy odczyt większej
próbki ujawnił wcześniejszy limit 4 MiB parsera; poprawiony decoder zachowuje
domyślne 4 MiB dla metadanych i wymaga jawnego limitu dla cech.

Pełny importer faktów trwał 8,52 s, curated 38,54 s, features build 28,68 s,
odtworzenie features 32,95 s, a wyznaczenie splitu 0,38 s. Niezależne
build/replay etykiet trwały 14,92 / 15,70 s. Szczyt RSS samego procesu
odbioru wyniósł 175 816 704 bajty, około 167,7 MiB. To kumulacyjny
`ru_maxrss` całej próby; nie jest pomiarem osobnego treningu ani całego
drzewa procesów. Hashe wszystkich 225 plików obu wejść pozostały identyczne.

## Podział w czasie

| Okres | Origin w UTC | Dopuszczone | Negatywne / pozytywne development |
|---|---|---:|---:|
| Train | [2026-04-21, 2026-06-05) | 340 | 193 / 147 |
| Tune | [2026-06-05, 2026-06-24) | 135 | 73 / 62 |
| Calibration | [2026-06-24, 2026-07-13) | 130 | 67 / 63 |
| Test | [2026-07-13, 2026-08-01) | 131 | Bez raportowania klas lub metryk |

Wszystkie cztery okresy są niepuste i development ma obie klasy, stąd
`temporal_membership_ready=true`. Wykluczono 279 punktów z powodu granicy
okna lub późnej dostępności etykiety, 432 istniejące braki, 96 niepoprawnych
wejść, 77 niepełnych końcowych okien i 12 okien z nieaktywnym lifecycle.
736 dopuszczonych plus 896 wykluczonych daje wszystkie 1632 origin.

Raport nie oblicza klas, metryk, kalibracji ani progów testu. Adapter
waliduje schemat istniejących etykiet i metadane ich dojrzałości; nie jest
to kampania jakości final test. Dostęp development obejmuje tylko
train/tune/calibration po pełnym replay członkostwa. Zmiana wartości
etykiety testowej nie zmienia splitu ani liczników development.

## Kontrole i publikacja

Regresja cech, etykiet, granic splitu, curated oraz importera inventory
przeszła **144/144 testów w 213,26 s**. Nowe przypadki obejmują przyszłe
dostawy i rewizje planów, kanały wspólnego zapasu, brak pojedynczego kanału,
chwilowe zero, starą informację o zapasie, opóźnioną etykietę, dokładną
granicę siedmiu dni i próbę przepisania testu do train. Resealed features
są odrzucane po pełnym odtworzeniu, nie tylko kontroli hasha.

Wheel zbudowany ze sdist i zainstalowany bez zależności do osobnego katalogu
przeszedł odbiór CLI poza repozytorium. Build/verify/rebuild cech trwały
23,72 / 24,10 / 24,57 s, z wynikiem `published` / `verified` / `reused`.
Build etykiet trwał 11,26 s, split build/verify wraz z pełnym replay obu
wejść 34,75 / 36,27 s. Wszystkie trzy pliki mają dokładnie te same bajty,
ID i tryb `0600` co natywna próba. Pakiet producenta `data` był niedostępny;
moduły konsumenta pochodziły z zainstalowanego wheel. Szczyt RSS pojedynczego
procesu potomnego wyniósł 205 570 048 bajtów, około 196,0 MiB; to maksimum
kumulacyjne `RUSAGE_CHILDREN`, nie suma jednoczesnej pamięci całego drzewa.
Brak jawnej zgody na truth zatrzymał split z kodem 1 bez utworzenia wyniku.
Wejścia pozostały niezmienione.

Pełny lokalny `make ci-local` przeszedł: **1800/1800 testów w 1501,27 s**,
bez ostrzeżeń, Ruff, format 550 plików, mypy 330 plików, docs,
runtime/handoff/import/curated, wszystkie dotychczasowe kontrole forecasting,
kontrakty, sdist/wheel, konfiguracja Compose, remediation i AI 04 acceptance.
Wheel z końcowej budowy ma ten sam hash co zmierzony odbiór instalacyjny.
Oba skany gitleaks (historia Git i katalog) nie wykryły sekretów.
Wcześniejszą lokalną próbę przerwano po poprawce kodu: zmiana hasha
implementacji w trakcie testów spowodowała trzy niespójności tożsamości
curated. Ponowiona regresja curated przeszła 37/37 testów na stabilnym kodzie.
Końcowe bramki wykonano na stabilnym kodzie bez wyłączania testów.

Poprzedni zakres etykiet ma zielone oba Required CI na dokładnym
`172496bad2eace2e94485be495a2010e411af6a2`:
[PR](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37098508223)
i [push](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37098463661).
Nie są one odbiorem nowszego kodu cech; aktualizacja roboczego
[PR #14](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/14)
wymaga własnego Required CI nowego commitu. Nie jest to odbiór chronionego main.

## Granice odbioru

Nie fitowano imputacji, skalowania, class weights, kalibratora lub progów.
Zapis dwóch wariantów sprzedaży przygotowuje przyszłe ablation; nie zastępuje
porównania modeli. Historyczny forecast upstream pozostaje do zbudowania,
dlatego `upstream_forecast_ready=false`, `model_ready=false`, cały AI 08
nie jest ready. Odbiór pełnego profilu treningowego, LR/HGB, kalibracja,
ranking przy ustalonej capacity oraz registry/batch/read API są przed nami.
Usługi, AI 05 runtime i oryginalne artefakty AI 04 nie zostały zmienione.
