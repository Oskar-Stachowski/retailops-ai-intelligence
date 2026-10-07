# Karta modelu — AI 09 Keras CPU development 1.0.0

Właściciel: RetailOps AI Intelligence. Stan: **experimental / not_ready**.
Przeznaczenie: uczciwy challenger prognozy obserwowanej sprzedaży na CPU,
z oddzielnymi mean i median, do późniejszej wspólnej kampanii portfolio.
Nie jest zatwierdzonym release’em ani modelem stockout/anomaly.

Output grain: product/selling location/channel/origin/target/horizon 1–14.
Origin zamyka dzień UTC, a każdy target to osobna data D+h. Target oznacza
`observed_sales_units`; nie jest nieobserwowalnym popytem ani sprzedażą
skorygowaną o przyszłe braki zapasu. Wynik ma units bez zaokrąglenia.

Architektura i preprocessing są w [runbooku](../tensorflow-challenger.md),
zamrożone hyperparameters w [recipe](../../contracts/evaluation/v2/challenger.default.json).
Historia 28 dat, covariates znane w origin, missing/unknown masks, train-only
normalization i masked direct output zachowują shared prediction population.
Early stopping używa development validation; final test pozostaje nieotwarty.
Data seed 42 i initialization seed 42 są osobnymi polami.

Pierwsza wykonana kontrola miała 1 train window, 1 validation window,
14 eligible validation keys i 3 epoki. Dane są regresyjnym
`controlled-temporal-fixture`, bez odbioru standardowych profili AI.
Dotychczasowy evaluator v2 raportuje następującą diagnostykę CLI:

| Cel | Keras | Stały history28 reference |
|---|---:|---:|
| Median MAE, units | 88,49 | 21,50 |
| Mean MSE, units² | 3519,93 | 478,50 |
| Mean WAPE, ratio | 0,8076 | 0,3386 |
| Eligible coverage | 1,00 | 1,00 |

Są to pomiary tej małej fixture, na validation użytej również do early stopping.
Nie stanowią niezależnej oceny jakości. Wynik zachowuje `not_ready` przez małą
próbę i brak skalibrowanego interval, oraz mierzalne porażki median improvement
i mean MSE. Baseline nie jest ponownie wybierany na podstawie wyniku TF.
Nie przenosi się historycznych wyjątków jakościowych AI 04 v12.

Model jest zapisany jako obsługiwany MLflow Keras flavor `.keras`, razem
z podpisem `float32[-1,input_width] → float32[-1,14,2]`, normalization,
recipe, environment lock i pełnymi checksums. Reload zgodny numerycznie
w tolerancji rtol/atol 1e-6 przechodzi na macOS ARM64, również w świeżym
procesie i z zainstalowanego wheel poza checkoutem. Linux CPU jest osobnym
Required CI acceptance. Platformy nie mają deklarowanej bitowej identyczności.

Przykładowy retained execution bundle: 301 944 bajty; cały worker wraz
z importem/saving/reload 7,65 s, peak RSS około 577 MiB. To nie jest koszt
pełnego profilu. Instalację dependencies i preprocessing raportuje się
oddzielnie. Przekroczenie budżetu, niezgodny signature/checksum/normalizer
lub brak kompletnej próby blokują przyjęcie artefaktu.

Ograniczenia: brak osobnej kalibracji interval, brak pełnego `ai-training`,
brak wspólnej końcowej kampanii AI09 na nowych danych trzech
seedów/scenariuszy i review lifecycle. AI07/08 mają własny zamknięty odbiór;
nie zastępuje on tej kampanii. Brak gotowości nie jest passed gate.
Szczegółowe hashes, lineage kontrolnej fixture, koszt i negatywne wyniki
pozostają w [wersjonowanym receipt](../evidence/09-02-tensorflow-challenger.json).

## Odrębny model 09.3 — dane po AI 06

[Benchmark development](../evidence/09-03-forecast-development-comparison.md)
ma odrębny model ID, source/curated/features/split i wspólną populację wszystkich
baseline/RF/HGB/TF. Dotyczy 102-dniowego `ai-temporal-smoke`, seed 42,
240 train windows / 2710 eligible labels oraz 240 validation windows /
3040 eligible spośród 3360 kluczy. Wcześniejsze wyniki kontrolnej fixture
pozostają zachowane i nie są porównywane jako poprawa na tych samych danych.

Model `model-sha256-b23f2854960c49b9b3a9eb676e71ae1eb2cbeeb7df252db67f19fd98f057fa43`
ma input width 1484 i ten sam direct14 mean/median output. Early stopping
przywrócił epoch 17 po 21 epokach, bez zmiany frozen configuration.
MAE mediany 3,893832 wobec history28 3,994079 poprawia się o 2,51%,
poniżej wymaganego 5%. MSE średniej 44,689410 wobec 45,036080 jest niższe;
globalny bias −3,80% nie usuwa porażki median gate, segmentów ani braku interval.

Jest to diagnostyka na early-stopping validation. Natywny replay odtwarza
prognozy i metryki bez fitów. Budżety poszczególnych workerów są zaliczone,
ale cały benchmark miał około 1,27 GiB peak RSS. Dwie wcześniejsze próby
przerwane przy 1 GiB pozostały w evidence; większa skala nadal wymaga pracy.
Model nie ma zgody na promocję ani końcowej kwalifikacji portfolio.

## Odtworzenie przy mniejszej pamięci — 09.4

[Odbiór pamięci](../evidence/09-04-development-memory.md) zachowuje identyczne
wejścia treningu, raw outputs, wszystkie prognozy i metryki 09.3.
Współdzielenie immutable lineage, strumieniowy population digest i osobny
proces CPU reload dały 939.02 MiB całego drzewa w małej próbie z limitem 1 GiB.
To nie kwalifikuje większego profilu ani niezależnej jakości.
Nowy model ID i code binding znajdują się w receipt; stary model oraz jego
kod pozostają historycznym artefaktem. Architektura, seedy i konfiguracje
pozostały identyczne. Runtime i promocja nie zostały zmienione.

## Wspólny audyt development — 09.5

[Rejestr prób](../development-trial-registry.md) utrwala budżet nowych
uruchomień przed fitami i zachowuje również awarie oraz nierozliczone rezerwacje.
Jedenaście istniejących prób 09.3/09.4 jest retrospektywną historią z checksums,
bez ponownego treningu ani zmiany metryk tej karty. Odbiór kompletnego runnera
na małych fixture nie jest nową kwalifikacją jakości lub większego profilu.
Finalny audyt dostępu do testu portfolio pozostaje otwarty.
