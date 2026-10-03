# Porównanie modeli ryzyka na development

Ten przyrost dopasowuje LogisticRegression i HistGradientBoostingClassifier
do [czasowych danych AI 08](stockout-features.md). Nie promuje modelu,
nie zmienia runtime AI 05 i nie otwiera outcomes final test. Wszystkie
rodzice są ponownie weryfikowane; zapisany model lub poprawnie wyglądający ID
nie zastępują pełnego replay.

## Dane i warianty

CLI odtwarza cechy, historyczny upstream, comparison i prywatne etykiety.
Helper `development_labels` ponownie odtwarza split i udostępnia wyłącznie
train/tune/calibration. Ręczne przeniesienie testowego wiersza do train,
nawet z nowym hashem manifestu, jest odrzucane. Walidacja prywatnego
rodzica sprawdza też schemat rekordów testu; nie jest oceną ich outcomes.
Raport testu zawiera wyłącznie liczebność członkostwa.

Każda rodzina ma trzy warianty na dokładnie tych samych fizycznych kluczach:

| Wariant | Wejścia i rola |
|---|---|
| `without_upstream` | Wszystkie 18 bazowych wartości; może być wybrany na tune |
| `with_upstream` | Bazowe wartości i 3 cechy historycznej prognozy; może być wybrany na tune |
| `raw_sales_only` | Kontrola ablation bez `in_stock_sales_mean` i `days_of_supply_in_stock`; nie bierze udziału w wyborze modelu |

Kontrola cenzorowania zachowuje historyczne flagi/coverage zapasu.
Porównuje wpływ dwóch wartości liczonych z potwierdzonych dni dostępności;
nie usuwa całego kontekstu inventory i nie szacuje latent demand.
Przyszłe fakty, etykiety, identyfikatory produktów i daty nie są wejściami
numerycznymi. Kategorie to fizyczna lokalizacja i kategoria produktu
z jawnie zweryfikowanego `product_catalog`, znana w origin. Późniejszy
rekord nie przepisuje wcześniejszej kategorii.

## Dopasowanie i wybór

`TrainingPolicy` 1.0.0 zamraża jedną konfigurację na rodzinę, przed pomiarem
wyników. LR używa C=1, max_iter=1000 i tol=1e-8. HGB ma 80 iteracji,
15 liści, min_samples_leaf=20, learning_rate=0.1 i L2=1. Early stopping
jest wyłączony. Seed to 42, biblioteki są z istniejącego locka, a obliczenia
mają jeden wątek. Nie ma oversamplingu ani class weights.

Imputacja medianami jest dopasowana tylko na train; kolumna całkowicie
nieznana otrzymuje jawne techniczne zero i własny wskaźnik braku.
Każda kolumna ma wskaźnik braku, także gdy train nie zawierał null.
LR skaluje wartości i wskaźniki na train. HGB nie jest skalowany.
Słowniki obu kategorii powstają tylko na train i mają limit 32 wartości
każdy; nieznana późniejsza wartość ma wektor samych zer. ID produktu
nie jest kategorią modelu.

Train dopasowuje parametry. Tune wybiera rodzinę/wariant według average
precision, następnie Brier, przy remisie LR i wariant bez upstream.
Wybór zostaje zapisany przed dopasowaniem kalibratora. Kontrola ablation
nie może zostać przypadkowo championem. To wybór provisional w development,
bez zezwolenia na promocję lub gwarancji jakości.

## Oddzielny kalibrator

Stała regularizowana funkcja sigmoid jest dopasowana do surowych log odds
na czasowo późniejszym calibration, przy naturalnej częstości zdarzeń.
Wymaga co najmniej 10 przykładów każdej klasy; mniejsza próba daje
`not_evaluable` i brak kalibratora. Nie stosujemy isotonic na małej próbce.

Raport porównuje raw i sigmoid na calibration, ale wynik po dopasowaniu
jest jawnie **in-sample fit diagnostic**, bez oceny generalizacji lub
zaliczonej bramki kalibracji. Nie wybieramy rodziny na podstawie tych wyników.
Liczba kalibratorów o dodatnim nachyleniu jest ujawniona: odwrócony ranking
wymaga osobnego rozliczenia przed promocją. Późniejsza niezależna kampania
oceny musi mieć zamrożone modele i polityki oraz osobny rejestr dostępu.

Zapisane `fit_known_at` to granice wiedzy train/calibration. Predykcja
odrzuca użycie modelu lub kalibratora w wcześniejszym origin. Diagnostyka
dopasowania kalibratora ma odrębny zakres; nie jest historycznym servingiem.

## Metryki i pojemność obsługi

PR-AUC używa nieinterpolowanego `sklearn.average_precision_score`.
Raport podaje prevalence i no-skill AP, Brier oraz Brier stałej częstości
wyznaczonej na train, pomocnicze ROC-AUC i 5 binów reliability z licznikami.
Brak obu klas daje ranking `not_evaluable`, zamiast idealnej metryki.
Brier pozostaje policzalny dla jednej klasy. Segmenty obejmują kategorię,
fizyczną lokalizację i historyczne ograniczenie zapasu.

Zamrożona ilustracyjna capacity to top 20% kwalifikujących się fizycznych
punktów **w każdym origin**, z zaokrągleniem w górę i remisem po kluczu.
Nie wybieramy top 20% ze wszystkich dat razem. Raport obejmuje recall,
precision, liczbę wybranych punktów i pojemność każdego origin.
Segmentowe capacity są liczone wewnątrz danego segmentu; nie należy ich
sumować jako jednej globalnej kolejki.

Tabela progów 0.1/0.25/0.5/0.75/0.9 ma stały, eksploracyjny koszt:
1 za fałszywy priorytet i 5 za pominięte zdarzenie. To jednostki diagnostyczne,
nie zatwierdzone koszty operacyjne ani oszczędności. Nie dopasowano progów
low/medium/high/critical; `threshold_policy_ready=false`.

Dobowe siedmiodniowe etykiety mogą dotyczyć wspólnego epizodu i nie są
niezależnymi przykładami. Temporal smoke dowodzi działania ścieżki,
bez deklaracji przewagi na pełnym profilu lub wymaganej liczebności segmentów.

## Przenośne artefakty i użycie

Pełny preprocessing, współczynniki LR, drzewa HGB i sigmoid są zapisane
jako dane JSON, bez pickle lub wykonywalnych estimatorów. HGB zachowuje
ujemne log odds; nie stosuje ograniczenia prognoz sprzedaży do zera.
Przy dopasowaniu weryfikowana jest zgodność eksportu z natywnym sklearn.

Manifest wiąże wszystkich rodziców, code/lock/library versions,
rozłączne hashe kluczy i etykiet development, stan modeli, wyniki i wybór.
Pakiet `stockout_training` ma osobną tożsamość od przygotowania danych;
dopasowanie modeli nie zmienia historycznych ID cech lub etykiet.
`verify` odtwarza rodziców i całe dopasowanie, odrzucając także ponownie
zahashowane zmiany. Limit wynosi 10 000 punktów i 16 MiB outputu;
nie jest to odbiór zasobów pełnego `ai-training`. Zapis jest atomowy,
prywatny `0600`, powtarzalny i nie nadpisuje innej treści.

Przygotuj istniejący prywatny katalog output i uruchom:

```sh
python -m retailops_ai.stockout_training.cli build \
  --curated /path/to/curated \
  --features /path/to/features.json \
  --upstream /path/to/upstream.json \
  --comparison /path/to/comparison.json \
  --labels /path/to/labels.json \
  --split /path/to/split.json \
  --source /path/to/private-inventory-snapshot \
  --output /path/to/private-output/development.json \
  --allow-evaluation-truth
```

`verify` używa tych samych parametrów. Jawna zgoda dotyczy weryfikacji
prywatnego źródła etykiet; nie daje dostępu do oceny final test.
[Odbiór development](../evidence/08-04-stockout-models.md) zapisuje wykonane
pomiary i ograniczenia. Model card/importance, pełny profil, niezależna
ocena kalibracji, threshold policy oraz registry/batch/API wymagają
późniejszych odbiorów. Cały AI 08 pozostaje otwarty.
