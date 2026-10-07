# AI 09 — rzeczywisty challenger TensorFlow na CPU

Status AI 09: **in_progress**. Przyrost 09.2 dodaje rzeczywisty trening Keras,
podpisany wymiarami MLflow artifact, preprocessing oraz ponowny odczyt na CPU.
Małe dane kontrolne potwierdzają wykonanie ścieżki. Nie są końcową oceną jakości
ani kwalifikacją `ai-training`; final test portfolio pozostaje nieotwarty.

## Model i dane

Wspólne typed `InputRow`, `Membership`, `LabelPoint` i `HistoryContext` wiążą
produkt, miejsce, kanał, origin, target i horyzont z istniejącym feature/split
manifestem. Czytnik wykorzystuje dotychczasowy weryfikowany indeks SQLite.
Train i development validation są rozdzielone istniejącym chronological foldem.
Nie konsumuje `development_holdout` do treningu ani early stopping. Weryfikacja
splitu może czytać jego wcześniejsze dane development; nie czyni ich nowym
niezależnym holdoutem. Ten format nie zawiera final testu portfolio.

Wejście obejmuje 28 dat historycznych z osobnymi wskaźnikami missing/inactive
oraz do 14 zestawów covariates znanych w tym samym origin. Nieznane przyszłe
obserwacje sprzedaży nie stają się covariates. Target-day plany i kalendarz
zachowują istniejącą politykę dostępności. Brak horyzontu ma jawny wskaźnik;
niepełna grupa nie usuwa pozostałych kwalifikujących się kluczy.

Istniejący train-only encoder dopasowuje mediany/mody i one-hot vocabulary.
Osobne missing/unknown categories pozostają kontrolowanym fallbackiem.
Centra i odchylenia standardowe są obliczane strumieniowo tylko na train;
stała kolumna ma scale=1. History imputation i skala targetu także pochodzą
wyłącznie z train. Normalizer zachowuje hash train windows i parents.

Keras ma warstwy Dense **32 → 16 → 28**, reshape **14 × 2**, ReLU w hidden
layers i softplus dla nieujemnych outputów. Wyjścia to osobno **mean/MSE**
i **median/MAE**. Loss sumuje te dwa cele po train scaling i maskuje niekwalifikujące
się targety. Trening używa Adam, stałej kolejności batchy i development validation
do early stopping z przywróceniem najlepszego epoch. Jedna zamrożona konfiguracja
nie jest nieograniczonym tuningiem. Globalny rejestr prób kampanii pozostaje
zakresem następnego przyrostu.

Typed wrapper zwraca istniejący `FunctionalForecast`. Interval pozostaje **null**
z `interval_status=not_ready`: jego osobna kalibracja i ocena są jeszcze wymagane.
Nie zastępuj mediany średnią ani brakujących przedziałów arbitralnym procentem.
Porównanie development używa tego samego evaluator v2 i dokładnie tych samych
kluczy względem jawnego, stałego history28 reference. Zachowuje null WAPE przy
all-zero actuals, liczebność, coverage oraz wszystkie mierzalne porażki.
Validation użyta do early stopping nie jest niezależnym dowodem jakości.

## Środowisko i zasoby

[Osobny projekt](../environments/tensorflow/pyproject.toml) i jego
[uv.lock](../environments/tensorflow/uv.lock) przypinają Python 3.11.15,
TensorFlow 2.20.0, Keras 3.11.3, MLflow skinny 3.4.0, NumPy 2.2.6 i SciPy 1.17.1.
Obsługiwane cele to macOS ARM64 (`tensorflow`) oraz Linux x86_64 (`tensorflow-cpu`).
Integracja po AI 07–08 zastępuje SciPy 1.15.3: świeże odtworzenie na macOS
odrzucało jego bibliotekę PROPACK jako niepoprawny Mach-O. To blokowało drzewa
oraz inferowanie tensorowego podpisu MLflow, mimo ukończonego treningu Keras.
Nowy lock i wymagania w zapisywanym bundle są zgodne; historyczne receipty
zachowują wcześniejsze środowisko i nie są nowym odbiorem tych zależności.
Główny `uv.lock` i środowisko v12 pozostają bez zmian. Nie ma CUDA extras.
Końcowy release wymaga rzeczywistego reload acceptance na swojej architekturze.

[Recipe](../contracts/evaluation/v2/challenger.default.json) ogranicza próbę
do 25 epok, 1200 s wall/CPU, 1024 MiB RSS i pojedynczych skonfigurowanych
wątków TF/BLAS. Supervisor mierzy własne drzewo procesu co 50 ms, weryfikuje
końcowe CPU/OS peak RSS i przerywa wyłącznie własnego workera. POSIX CPU limit
dodatkowo ogranicza worker. Sam preprocessing jest osobno mierzony i bounded,
nie wliczany do czasu workera. Budżet macierzy jest sprawdzany przed alokacją;
limit 3000 windows/64 MiB dotyczy development. Większy profil jest odrzucany,
nie obcinany ani nazywany `ai-training`.

Każda próba ma nowy prywatny katalog i zachowany `attempt.json`, także po
odrzuceniu. Retry nie nadpisuje istniejącego katalogu. Model `.keras` zapisuje
obsługiwany MLflow Keras flavor z input/output signature; ten sam bundle zawiera
preprocessing, recipe, pełny dependency lock i checksums każdego pliku.
Nie ma własnego pickle loadera ani wymaganych custom layers.
Ładowanie sprawdza tożsamość, zakończenie próby, budżet, preprocessing,
actual MLflow signature, lock i implementację przed uruchomieniem modelu.
Run MLflow jest lokalny i izolowany w katalogu próby; nie zmienia wspólnego registry.
Determinism jest włączony dla ustalonej platformy, wersji i init seed;
nie deklaruje byte identity między macOS i Linux.

## Polecenia

```bash
uv sync --locked --project environments/tensorflow

uv run --locked --project environments/tensorflow \
  python -m retailops_ai.tensorflow_challenger.cli train-development \
  --features /path/to/features-sha256-ID \
  --split /path/to/split-sha256-ID --fold fold-a \
  --output /private/tmp/ai09-new-development-attempt

uv run --locked --project environments/tensorflow \
  python -m retailops_ai.tensorflow_challenger.cli verify \
  --artifact /private/tmp/ai09-new-development-attempt

make tensorflow-check
```

CLI zwraca 0 dla poprawnego treningu/artefaktu, 2 dla błędu. Sukces procesu
nie zmienia `deployment_status=not_ready` ani `promotion_allowed=false`.
Make check/ci-local zawiera osobny locked TensorFlow check, więc Required CI
wykonuje również rzeczywisty trening i świeży reload Linux CPU.

## Pozostały odbiór

Pełna kampania wymaga nowych kwalifikowanych source/curated/features po 06,
odbiorów AI 07/08, większego czytnika/batch treningu, niezależnego fair comparison,
osobnej development calibration, globalnego audytu prób i final-test access,
zamrożonych segmentów/wag/gates, trzech data seeds 42/137/2026 oraz trzech
model cards i decyzji lifecycle. Wyjątki jakościowe AI 04 v12 nie są dziedziczone.
[Evidence 09.2](evidence/09-02-tensorflow-challenger.md) podaje wykonane wyniki.
[Karta modelu development](cards/tensorflow-development.md) zachowuje również
gorszy wynik względem history28 i ograniczenia kontrolnej próby.
[Przyrost 09.3](forecast-development-comparison.md) dodaje rzeczywiste wspólne
porównanie baseline/RF/HGB/TF na development, z osobnymi artefaktami i replay.

Źródła sposobu serializacji i instalacji:
[TensorFlow installation](https://www.tensorflow.org/install/pip),
[MLflow Keras API](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.keras.html),
[TensorFlow determinism](https://www.tensorflow.org/api_docs/python/tf/config/experimental/enable_op_determinism).
