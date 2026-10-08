# Pełne portfolio scenariuszy AI 09

Dotychczasowy v10 dopuszcza cztery źródła: jeden development i trzy końcowe
seedy. Generator Source 2.8 przyjmuje jeden plan demand albo physical na
generację. Jedna receptura nie pokrywa więc obu rodzajów zaplanowanej zmiany.
[Rozszerzenie v25](../contracts/evaluation/v25/full_scenario_portfolio_protocol.schema.json)
rejestruje cały wymagany zakres w jednym dzienniku, z jednym wspólnym freeze.

| Faza i seed | Warianty | Rozmiar każdego źródła |
| --- | --- | --- |
| Development 42 | ordinary, demand, physical | 365 dni, 100 produktów, 5 par sprzedaży, 3 lokalizacje zapasu |
| Final 42 | ordinary, demand, physical | 730 dni, 200 produktów, 10 par sprzedaży, 4 lokalizacje zapasu |
| Final 137 | ordinary, demand, physical | 730 dni, 200 produktów, 10 par sprzedaży, 4 lokalizacje zapasu |
| Final 2026 | ordinary, demand, physical | 730 dni, 200 produktów, 10 par sprzedaży, 4 lokalizacje zapasu |

To 12 pełnych źródeł. Ordinary zachowuje naturalny normal i znane promocje,
demand dostarcza zaplanowane demand shocks, a physical musi zawierać
`inventory_censored_episode`. Sam plan return spike nie zastępuje scenariusza
ograniczonego zapasu. Te deklaracje wymagają później rzeczywistych liczebności,
pokrycia i efektów w raportach; nazwa wariantu nie dowodzi wykonania scenariusza.

Warianty tej samej fazy i seeda mają identyczne pełne parametry bazowe oraz
osobne, przypięte hashe oryginalnych planów. Wszystkie źródła używają jednego
producenta i jego locków. Publiczne `CampaignGenerationPlan.bind` sprawdza
rodzaj planu i jego dokładny hash przed wejściem do istniejącego runnera.
Normalny Source writer, reader i niezależny replay pozostają obowiązkowe.

Protokół wskazuje osobne, zamrożone źródło treningu dla każdego zastosowania.
Forecast RF/HGB/TensorFlow zachowuje wspólny zakres i równe budżety prób oraz
seedów inicjalizacji. Zależność między wariantami development może przenosić
wyłącznie zadeklarowany artefakt tego samego zastosowania; odczyt własnego
rodzica nadal wymaga własnej zakończonej generacji. Final nie pożycza mutowalnej
gałęzi development. Źródła i wszystkie odczyty pozostają jawnie rozliczone.

Przed wspólnym freeze muszą zakończyć się odczyty całego development oraz
ocena wszystkich trzech zastosowań w każdym z trzech wariantów. Każdy
zadeklarowany fit musi mieć próbę w dzienniku; awaria pozostaje rozliczona,
a niewykonana próba nie może zniknąć za wybranym zwycięzcą. Dotychczasowe guardy
wymagają również zakończonego fitu każdej rodziny forecastu i rozstrzygnięcia
wszystkich rezerwacji. Końcowe zamknięcie obejmuje wszystkie trzy zastosowania
na każdym z dziewięciu źródeł final. Generacja każdego źródła ma tylko jeden slot.

V10 i jego schemy pozostają niezmienione. V25 ma jawne
`portfolio_version=ai09-full-scenario-portfolio-1.0.0`, a `version` zachowuje
wersję protokołu bazowego. Czytnik dziennika wybiera właściwy ścisły kontrakt
i zachowuje całą rozszerzoną strukturę przy reload, rezerwacji i publikacji.

[Receipt 09.41](evidence/09-41-full-scenario-portfolio.json) oddziela testy
metadata i trwałego dziennika od wykonania naukowej kampanii. Przykładowe
68 operacji/78 slotów w testach nie są zamrożonym protokołem projektu.
Native control sprawdza oryginalne kontrakty planów oraz 12 publicznych bindings
na pełnych metadanych, z jawnymi kontrolnymi IDs i wcześniej eksponowanymi
oknami. Nie generuje datasetów ani nie kwalifikuje aktywności serii, efektów
fizycznych, zasobów lub świeżości final.

Do rzeczywistego wykonania pozostają odbiór pełnej pojemności, kompletne
produkcyjne receptury i budżety oraz adaptery oceny modeli między wariantami.
Dotychczasowy evaluator development wymaga tego samego dataset ID co frozen
configuration; ten guard nie został osłabiony. Potrzebne są także rzeczywiste
evaluatory anomaly/stockout, jawna polityka krytycznych segmentów i weryfikacja
nieeksponowanych końcowych okien przed inicjalizacją Project. Samo zamknięcie
lokalnego dziennika nadal nie nadaje jakości, świeżości ani statusu AI 09 ready.
