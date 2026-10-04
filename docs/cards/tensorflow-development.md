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
brak finalnych wyników AI 07/08, brak niezależnej końcowej ewaluacji trzech
seedów/scenariuszy i review lifecycle. Brak gotowości nie jest passed gate.
Szczegółowe hashes, lineage kontrolnej fixture, koszt i negatywne wyniki
pozostają w [wersjonowanym receipt](../evidence/09-02-tensorflow-challenger.json).
