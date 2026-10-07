# AI 08.3 — historyczny baseline i wejścia do porównania

Przyrost dodaje cechę historycznej prognozy sprzedaży na 7 dni oraz
porównywalne wejścia modeli z nią i bez niej. [Kontrakt i polecenia](../reference/stockout-upstream.md)
określają granice wiedzy, mapowanie magazynów i brak dopasowania do outcomes.
[Wersjonowany odbiór](08-03-stockout-upstream.json) przypina kod, rodziców,
wyniki, politykę i pomiary. Bazą jest przyrost cech i splitu
`3265ed1ebbc5db85adde77c65722247936e5e684`, na main AI 05
`f4ae14fe6588b91506383a709b5a0185208a4ea6`.

## Natywna próbka 102 dni

Ponownie wykorzystano oba niezmienione snapshoty `ai-temporal-smoke`, seed 42,
2026-04-21–2026-07-31, 8 produktów i 2 fizyczne magazyny. Nie generowano
nowych danych. Źródło pozostaje przypięte do producenta
`08639e9188badb352ed64686a088fe237badad41`. Hash 225 plików wejść przed
i po próbie jest identyczny.

| Zakres | Dostępna prognoza / liczba punktów |
|---|---:|
| Wszystkie origin, także istniejące braki | 1380 / 1632 |
| Bazowe cechy `eligible` | 1015 / 1104 |
| Dopuszczony train | 340 / 340 |
| Dopuszczony tune | 135 / 135 |
| Dopuszczony calibration | 130 / 130 |
| Członkostwo późniejszej oceny testowej | 131 / 131 |

252 niedostępne prognozy obejmują 112 punktów z za krótką historią,
112 bez pełnego znanego przyszłego asortymentu i 28 bez aktywnego mapowania
w origin. Dla wszystkich 736 okien dopuszczonych przez split prognoza jest
dostępna. Liczniki testu dotyczą wyłącznie wejść i członkostwa, bez
raportowania klas lub metryk outcomes. Globalne `upstream_forecast_ready`
pozostaje false: dla 89 z 1104 bazowych wierszy nie ma pełnej prognozy.
Nie przepisano ich na zerowe prognozy ani na zaliczone. Przy treningu
konieczne pozostaje jawne powiązanie z dopuszczonymi oknami kampanii.

Upstream ma 2 127 982 bajty, około 2,03 MiB; porównywalne wejścia modeli
1 407 074 bajty, około 1,34 MiB. Oba są poniżej osobnych limitów 16 MiB.
Przyrost zawiera 1632 wspólne klucze, 0 odrzuceń specyficznych dla wariantu
i nie zawiera etykiet ryzyka. Bazowe wartości nie są zastępowane wynikiem
prognozy ani uczone na późniejszych outcomes.

Ponowna budowa bazowych cech trwała 26,12 s, historyczny upstream build
44,90 s, pełny replay 50,82 s, a budowa wejść do porównania 0,29 s.
Ponowna budowa etykiet z tej wersji kodu trwała 12,78 s; liczby splitu
340/135/130/131 są niezmienione. Kod wiąże artefakty z pełnym hashem modułów,
więc nowe ID cech/etykiet/splitu są oczekiwane. Poprzednie pliki i ich
zainstalowany wheel pozostają zachowane, bez nadpisywania historycznych ID.

Szczyt RSS samego procesu natywnej próby wyniósł 178 569 216 bajtów,
około 170,3 MiB. To kumulacyjny `ru_maxrss`, bez deklaracji budżetu
treningu modeli lub pomiaru pełnego drzewa procesów.

## Kontrole

Regresja upstream, cech, etykiet, splitów i istniejących baselines przeszła
**102/102 testów w 25,98 s**, w tym 16 nowych przypadków. Obejmuje późniejsze
fakty/revisions, dokładną granicę ostatniej sekundy dnia, znane zamknięcie
versus brak kalendarza, minimalne 7 znanych dni, dwa kanały wspólnego zapasu,
osobne magazyny i niejednoznaczny routing. Przyszły training/selection cutoff
i przyszła dostępność źródła są odrzucane. Pełny replay odrzuca także
zmodyfikowany i ponownie zahashowany upstream.

Mały fixture 10 dni zachowuje **60/60 `insufficient_data`** dla upstream:
brakuje dostatecznej historii lub pełnego siedmiodniowego kontekstu przyszłego
asortymentu. Nie udaje próby gotowego forecastu. Test negatywnego replay
zmienia jego cutoff; nie wymaga fikcyjnego dostępnego punktu.

Wheel zbudowany ze sdist, zainstalowany bez zależności do osobnego katalogu
i uruchomiony poza repozytorium przeszedł pełny odbiór CLI. Build/rebuild/verify
upstream trwały 98,82 / 87,36 / 90,64 s, z wynikiem
`published` / `reused` / `verified`; obejmują też pełne odtworzenie cech.
Comparison build/verify trwały 80,35 / 71,98 s wraz z replay obu rodziców.
Cechy, upstream i comparison mają dokładnie te same bajty i ID co natywny
odbiór oraz tryb `0600`. Pakiet producenta `data` był niedostępny.
CLI nie otrzymał etykiet, prywatnego snapshotu ani zgody na truth.
Wszystkie wejścia pozostały niezmienione. Maksimum RSS pojedynczego procesu
potomnego wyniosło 211 042 304 bajty, około 201,3 MiB; jest to kumulacyjny
`RUSAGE_CHILDREN`, nie suma pamięci całego drzewa.

Pełny `make ci-local` przeszedł 2026-10-03 o 07:12:23 UTC: **1816/1816 testów
w 1678,21 s**, lint/format 556 plików, mypy 333 modułów, wszystkie 21 targetów
`check` oraz skany sekretów historii i katalogu. Wheel z ponownej budowy
ma identyczny hash jak odebrany zainstalowany pakiet. Kontrola zdalna
tego commita wymaga osobnego wyniku po publikacji w draft PR #14.

## Granice

Algorytm jest stałą historyczną średnią obserwowanej sprzedaży, nie
predictorem v12 i nie estymatą latent demand. `training_cutoff` wskazuje
granicę rekonstrukcji; nie deklarujemy wykonania historycznego treningu
lub istnienia dawnego release. Nie zmieniono oryginalnego AI 04, AI 05
runtime, registry, usług, batch lub API.

Przygotowano wejścia ablation, bez wyniku porównania modeli.
`ablation_model_results=pending`, `model_ready=false`; cały AI 08 nie jest
ready. Kolejny zakres to LR/HGB z preprocessingiem dopasowanym tylko na
train, oddzielna kalibracja development i capacity policy. Pełny profil,
kontrolowana final test campaign i integracja lifecycle/batch/API mają
własny późniejszy odbiór.
