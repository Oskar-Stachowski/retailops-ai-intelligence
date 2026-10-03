# Historyczna prognoza bazowa dla AI 08

Przyrost dodaje prognozę obserwowanej sprzedaży na kolejne 7 dni jako
dodatkową cechę [ryzyka nowego braku](stockout-labels.md). Przygotowuje też
identyczny grain dla wariantów z prognozą i bez niej. Porównanie jakości
LR/HGB, kalibracja oraz ablation modeli są kolejnymi krokami;
`model_ready=false`, `ablation_model_results=pending`.

## Jeden historyczny origin, bez dopasowania do przyszłości

Używamy istniejącego `forecasting.baselines.predict`: stałej średniej
z ostatnich 28 dat, wymagającej 7 znanych dni dla każdej serii sklep/kanał.
Brakujące dni nie są zerami. Znane zamknięte dni mają potwierdzone zero.
Algorytm nie dopasowuje wag ani parametrów do etykiet ryzyka, nie wybiera
konfiguracji na późniejszych outcomes i nie używa oryginalnego predictora v12.
Polityka dopuszcza krótszą aktywną historię przy 7 znanych dniach;
informacyjna flaga krótkiej historii AI 04 nie zastępuje tej jawnej polityki.

Każdy origin dostaje oddzielną historię i prognozę. Dane najpierw są
odcinane przez dostępność, potem wybierane są wersje. Fizyczne wykonanie
próby dzisiaj jest jawną rekonstrukcją rolling-origin. `training_cutoff`
i `selection_cutoff` zapisują historyczną granicę wiedzy tej stałej receptury;
nie są deklaracją, że w tamtym dniu działał wytrenowany model lub Registry.
Polityka jawnie ma `fixed_algorithm_no_fitted_parameters` i
`fixed_recipe_no_outcome_selection`.

Inventory ma origin `23:59:59.999999`, a kontrakt prognoz AI 04 wymaga
`23:59:59`. Adapter zachowuje oryginalne `as_of` inventory i używa
konserwatywnego origin prognozy z tej samej daty, wcześniejszego o 0,999999 s.
Nie zaokrągla do następnej północy. Fakt lub poprawka dostępne dopiero
w tej ostatniej części sekundy nie trafiają do historycznej prognozy.
Origin inventory wcześniejszy niż ta granica jest odrzucany.

## Fizyczny magazyn i przyszłe plany

Prognozy zachowują rzeczywiste klucze selling location/channel AI 04.
Przypisujemy je do fizycznego magazynu przez route znany i aktywny w origin,
a następnie sumujemy każdą serię raz. Nie tworzymy fikcyjnego kanału
dla wspólnego zapasu. Niejednoznaczne przypisanie jednej serii do dwóch
magazynów zatrzymuje budowę.

Przyszły znany kalendarz i asortyment są dozwolonymi planami. Dzień
potwierdzonego zamknięcia daje prognozę 0; brak znanego kalendarza lub
asortymentu daje `insufficient_data` i `null` dla pełnej sumy siedmiu dni.
Niepełna prognoza jednej wymaganej serii nie daje częściowej sumy magazynu.
Rzeczywiste przyszłe dostawy, inventory outcomes i latent demand nie są
tabelami wejściowymi tego algorytmu. Prognoza nadal dotyczy sprzedaży,
która może być ograniczona brakiem zapasu; nie zastępuje analizy cenzorowania.

## Pochodzenie i odtworzenie

Builder ponownie weryfikuje curated 1.1 i digest wybranych typed tabel
po odczycie. Zbiór bazowych cech musi być w pełni odtworzony, mieć ten sam
curated parent i kod. CLI robi to przed budową i weryfikacją upstream.
Nie wystarcza podanie poprawnie wyglądającego dataset ID.

Manifest przypina bazowy feature dataset, source/curated/qualification,
politykę i kod konsumenta oraz kod AI 04. `upstream_model_version` jest
hashem stałej receptury i implementacji baseline; nie jest wersją MLflow.
Każdy punkt przechowuje origin, cutoffs i dostępność, a każda seria hash
route, kontekstu historii, pełnych wejść forecast i 7 prognoz dobowych.
Weryfikacja odtwarza cały wynik. Zmiana prognozy, cutoffs lub kontekstu
i ponowne przeliczenie hashów nie pozwalają zaakceptować artefaktu.

Limity pozostają 64 MiB / 500 000 wierszy wejścia i 10 000 fizycznych origin.
Upstream i porównywalne wejścia modeli mają osobny limit 16 MiB.
Publikacja jest atomowa, prywatna (`0600`), bez nadpisania innej treści;
powtórzenie zwraca `reused`.

## Porównywalne wejścia modeli

Artefakt `stockout_comparison_inputs` zawiera wspólne klucze i statusy
bazowych cech. `without_upstream` używa ich jawnej listy wartości;
`with_upstream` dodaje sumę prognozy na 7 dni, days of supply według tej
prognozy i flagę jej niedostępności. Gdy suma wynosi zero, days of supply
pozostaje `null`. Nieznana prognoza także pozostaje `null`, z flagą 1.

Oba warianty mają dokładnie te same wiersze. Nie odrzucamy punktów tylko
z jednego wariantu, nie zapisujemy outcomes i nie fitujemy imputacji,
skalowania lub class weights. Późniejszy trening musi używać członkostwa
[czasowego splitu](stockout-features.md); ewentualną imputację fitować
wyłącznie na train, kalibrację na oddzielnym development calibration,
a dostęp do outcomes final test ograniczyć do zatwierdzonej kampanii.

`upstream_lineage_status=passed` oznacza poprawną rekonstrukcję i lineage.
`upstream_forecast_ready` jest konserwatywną kontrolą kompletności dla
wszystkich bazowych wierszy `eligible`. Może pozostać false mimo pełnego
pokrycia później dopuszczonych okien splitu: punkty blisko końca historii
lub nieaktywności produktu nie mają pełnej prognozy. Liczniki pokazują
oba zakresy; brak danych nie jest ukrywany przez zmianę statusu.

## Polecenia offline

```sh
python -m retailops_ai.stockout.prepare upstream-build \
  --curated /path/to/curated \
  --features /path/to/private-output/features.json \
  --output /path/to/private-output/upstream.json

python -m retailops_ai.stockout.prepare upstream-verify \
  --curated /path/to/curated \
  --features /path/to/private-output/features.json \
  --output /path/to/private-output/upstream.json

python -m retailops_ai.stockout.prepare comparison-build \
  --curated /path/to/curated \
  --features /path/to/private-output/features.json \
  --upstream /path/to/private-output/upstream.json \
  --output /path/to/private-output/comparison.json
```

`comparison-verify` używa tych samych parametrów i odtwarza istniejący wynik.
Te akcje czytają publiczne curated i nie potrzebują pliku etykiet ani zgody
na evaluation truth. Odtworzenie musi używać przypiętej wersji kodu,
locka i Python. [Odbiór](../evidence/08-03-stockout-upstream.md) opisuje
natywną próbkę 102 dni i jej granice.
