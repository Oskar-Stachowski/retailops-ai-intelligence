# AI 09.3 — porównanie development na natywnych danych po AI 06

[Runbook](../forecast-development-comparison.md) i
[receipt JSON](09-03-forecast-development-comparison.json) wiążą rzeczywisty
benchmark baseline/RF/HGB/TensorFlow. AI 09 pozostaje **in_progress / not_ready**.
Nie ma końcowej oceny portfolio ani promocji modelu.

## Dane i porównywalność

Użyto istniejącego source 2.7, seed 42, `ai-temporal-smoke`: 8 produktów,
3 selling locations, 2 fizyczne magazyny i 102 dni (28 warmup / 60 origins /
14 tail). Nie regenerowano źródła ani nie zmieniano danych sesji AI 08.
Kopia niezmiennego curated 1.1 ma identyczne bajty; oryginał zachował hashes.
Kopia i nowe features/split znajdują się w prywatnym katalogu AI 09.

Powstało 20 076 feature rows. Fold `development-v1` ma po 240 okien train
i validation, po 3360 kluczy. Train ma 2710 eligible labels, validation 3040.
Każdy z sześciu wariantów zachował **wszystkie 3360 kluczy validation**,
łącznie 20 160 zapisanych predykcji. Wyłączone punkty mają null i te same
powody wyłączenia. TensorFlow nie usuwa niepełnych horyzontów ani trudnych kluczy.

Protocol został zapisany przed pierwszym fitem. Wszystkie rodziny używają tej
samej wiedzy historycznej, mature train labels i rodziców. Encoder/normalizer
korzysta wyłącznie z train. Jedna konfiguracja każdej rodziny jest zamrożona,
reference to stały history28. RF uczy mean; HGB i TF mają odrębne mean/median.
Nie podstawiono średniej RF jako mediany.

## Wynik — diagnostyka validation

| Model | MAE mediany | MSE średniej | WAPE średniej | Bias średniej |
| --- | ---: | ---: | ---: | ---: |
| history7 | 4,043421 | 46,585088 | 73,30% | −10,07% |
| history28 — stały reference | 3,994079 | 45,036080 | 72,88% | −7,59% |
| weekday28 | 4,130263 | 50,742946 | 73,81% | +1,55% |
| RF mean | nieobsługiwana | 50,292252 | 73,28% | −1,84% |
| HGB mean/median | 4,059672 | 58,357086 | 73,88% | +12,09% |
| TensorFlow mean/median | 3,893832 | 44,689410 | 69,53% | −3,80% |

TF ma około **2,51%** niższe MAE mediany niż history28, co nie spełnia
wymaganego globalnego progu 5%. MSE jest niższe o około 0,77%, a bias mieści
się w globalnym limicie; te wyniki nie usuwają porażki MAE ani brakujących
przedziałów. RF/HGB mają regresję MSE; HGB przekracza także globalny bias.
Nie dostrojono modeli po tym wyniku ani nie wybrano nowego championa.

Raport zachowuje globalne wyniki oraz 14 horyzontów, 8 kategorii, 2 kanały
i 4 koszyki wolumenu dla każdego wariantu. TF ma mierzalne porażki w 7 ocenach
horyzontów, 7 kategorii, 2 kanałów i 1 koszyka. Koszyki zero/high mają za małą
próbkę. `not_ready` z powodu brakujących przedziałów nie ukrywa tych porażek.
Surowe empiryczne przedziały baseline'ów nie są osobną kalibracją.

Validation została wykorzystana do early stopping TF. Jest to **diagnostyka
development, nie niezależne zaliczenie jakości**. Istniejący verifier splitu
odczytuje wcześniejsze etykiety development holdout; nie wykorzystano tej roli
do treningu, wyboru lub metryk benchmarku i nie uznano jej za nowy holdout.
Final test portfolio nie występuje w tym formacie i pozostaje nieotwarty.

## Koszty i zachowane awarie

Przygotowanie kopii curated/features/split trwało 275,11 s. Sam benchmark
z weryfikacją rodziców, fitami, reload, predykcjami i raportem trwał **113,24 s**,
obserwowane CPU drzewa 108,77 s, peak RSS **1300,36 MiB**. Sampler co 50 ms
mierzył własne drzewo bez samplera; pomiar nie kontroluje cache i całej aktywności
komputera. Procesy miały nice=15 i pojedyncze skonfigurowane wątki.

RF fit miał 2,80 s / 319,30 MiB RSS, HGB mean 1,76 s / 234,08 MiB,
HGB median 1,87 s / 238,30 MiB. TF worker miał 8,75 s / 589,06 MiB,
w tym właściwy trening 1,63 s. Early stopping zakończył 21 epok i przywrócił
epoch 17. Cold load 0,011 s w receipt workera dotyczy już zaimportowanego
frameworka; nie jest kosztem uruchomienia TensorFlow w nowym procesie.
Łączny reload i prediction wszystkich wariantów miał 7,42 s.

Dwie wcześniejsze próby z limitem **1 GiB całego procesu** zostały przerwane
przez własny supervisor: 96,995 s / 1026,34 MiB oraz 94,800 s / 1025,00 MiB.
Oba katalogi, zamrożone konfiguracje, dzienniki i external interruption receipts
pozostały zachowane, bez completed manifest. Zwolnienie modeli drzew przed TF
nie zaliczyło tego budżetu; nie deklaruje się udowodnionej poprawy peak RSS.

Trzecia próba miała przed startem odrębny budżet całego procesu 1200 s wall/CPU
i **1,5 GiB RSS**. Limity poszczególnych modeli pozostały niezmienione i zostały
zaliczone. **Budżet 1 GiB całego benchmarku pozostaje niezaliczony.** Próby
przerwano przed zakończeniem oceny TF; konfiguracji jakościowej nie zmieniano.

Completed artifact ma 75 plików / 16 066 045 B. Natywny verifier ponownie
sprawdził rodziców, preprocessing, model signatures, predykcje i raport bez fitów:
86,78 s / 1139,78 MiB. Model IDs, prognozy i metryki były identyczne.

## Kontrole i pozostały zakres

85 ukierunkowanych testów zaliczono w 1,68 s. Oddzielny zestaw CPU sprawdza
rzeczywiste RF/HGB/TF fity i reload bez nowych fitów. Odłączony wheel poza
checkoutem odtworzył identyczne prognozy i metryki w 88,72 s bez fitów;
wszystkie 53 załadowane moduły konsumenta pochodziły z zainstalowanego pakietu.
Pełny `make ci-local` zakończył się kodem 0: **1815 testów głównych w 1353,69 s**,
bez ostrzeżeń, **3 rzeczywiste testy TensorFlow CPU w 40,42 s**, wszystkie
checkery, kontrakty, pakiet, Compose config i oba skany sekretów. Końcowa
aktualizacja receipt ma dodatkowe kontrole docs/secrets. Poprzedni `9ddd1f5` ma zaliczone
[Required CI z Linux CPU](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37190248558);
nowy przyrost wymaga własnego CI po publikacji.

Osobne calibration/evaluation partitions, większy bounded reader i batch
training, globalny trial/access journal, 3 seedy i scenariusze, time/series
uncertainty, ostateczne AI 07/08 policies i trzy karty lifecycle pozostają otwarte.
Ten przyrost nie kwalifikuje pełnego `ai-dev` ani `ai-training`.
