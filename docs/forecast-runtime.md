# AI 05.5a — przygotowanie wejścia i loader release’u

Ten zakres dostarcza prywatny pakiet wejściowy oraz loader przypiętego,
zatwierdzonego release’u dla RF, HGB i baseline’u. [Odbiór](evidence/05-05-runtime.md)
potwierdza obliczenia na rzeczywistych, archiwalnych artefaktach AI 04 oraz
odmowy w testach jednostkowych. AI 04 pozostaje `not_ready`; nie załadowano
rzeczywistego zakwalifikowanego release’u i nie opublikowano prognoz.

## Pakiet wejścia

Przygotowanie czyta formalny pakiet features i jego curated parent.
Oba przechodzą pełną weryfikację istniejącymi walidatorami AI 03/04;
source/curated ID, checksum descriptor i forecast-source readiness muszą
się zgadzać. Partycje są sprawdzane także podczas odczytu, a ponowna
weryfikacja features wykrywa zmianę całego pakietu w trakcie przygotowania.

`inputs.json` zawiera formalny feature manifest, origin na koniec dnia UTC,
scope, horyzont 7 albo 14, uporządkowane wiersze i przypiętą historię.
Wymagany jest dokładny iloczyn produktu × lokalizacji × kanału × dziennego
horyzontu, bez braków, duplikatów i dodatkowych serii. Historia, jej SHA,
liczniki oraz cechy lag/rolling muszą się zgadzać. Typed kontrakt odrzuca
cechy i referencje dostępne po origin; nie przyjmuje truth ani etykiet.

Limit to 20 produktów × 5 lokalizacji, jeden kanał, 1400 wierszy,
100 historii i 32 MiB JSON. `profile_id` jest hashem całej treści.
Publikacja prywatnego pakietu jest atomowa, bez nadpisania, w katalogu
0700 z plikiem 0600. Powtórzenie tej samej treści zachowuje istniejące
bajty. Odczyt odrzuca symlinki, dodatkowe pliki, duplicate JSON keys,
nonfinite numbers i zmienioną treść.

Pakiet przygotowuje zaufany proces z dostępem do danych. Hash potwierdza
integralność, a nie uprawnienie użytkownika ani autentyczność dowolnego
pliku klienta. [Prywatny rejestr PostgreSQL 05.5b](forecast-input-store.md)
przechowuje zweryfikowane wejścia. Nie ma publicznego uploadu ani przyjęcia
takiego profilu do kolejki. Format nie zastępuje grantów i scope API.

Przygotowanie i ponowna kontrola, po zainstalowaniu extras `snapshot` i `forecast`:

```bash
python -m retailops_ai.forecast_jobs.runtime_cli inputs-build \
  --feature-dir /private/path/features \
  --curated-dir /private/path/curated \
  --as-of 2026-07-12T23:59:59Z \
  --product product-id --location location-id --channel online \
  --horizon 14 --output-root data/generated/inference-inputs

python -m retailops_ai.forecast_jobs.runtime_cli inputs-verify \
  --inputs-dir data/generated/inference-inputs/batch-profile-sha256-<64-hex>
```

`--product` i `--location` można powtarzać. Origin musi istnieć w zweryfikowanych
features; przygotowanie nie generuje nowych danych ani nie trenuje modelu.
Pakiety trafiają do ignorowanego `data/generated/`, nie do Git.

## Zatwierdzony release i zgodność danych

`load_release` przyjmuje dokładny release, rzeczywiste piny środowiska
oraz zaufany Registry. Wymaga namespace `retailops-demand-forecast`,
`qualified_forecast`, wszystkich bramek `passed`, identycznego image digest
i dependency lock. Registry ponownie waliduje niezmienną wersję, quality
evidence, lineage, freshness, signature i load smoke. Następnie loader
sprawdza rozmiar i SHA dokładnie tych bajtów model/config/signature, których
użyje; wcześniejsza walidacja nie zastępuje tej kontroli.

Capsule `config.json` musi zawierać pełne `feature_policy`, przypięte
receipt i `config_sha256`. Signature zachowuje treningowy feature ID,
checksum typed input schema, `observed_sales_units` i `nonnegative_units`.
RF/HGB dodatkowo wiążą rodzinę, split, lock i preprocessing z qualification.
Nie używamy pickle ani ponownego rozwiązania aliasu po załadowaniu.

Nowy pakiet inferencji może mieć inny feature ID niż dane treningowe.
Musi zachować tę samą politykę cech, schemat i dependency lock.
Inferencja używa zamrożonych median, kolejności kolumn i słownika kategorii;
nowa kategoria trafia do istniejącego kodowania unknown, bez refit.
Dotychczasowe metody diagnostyczne AI 04 nadal wymagają treningowego feature ID.

Origin musi być późniejszy niż `selection_cutoff` modelu i nie może być
w przyszłości. Niewystarczająca lub nieświeża historia oraz nieznany albo
zamknięty kalendarz celu blokują cały batch. Obliczenia mają chunk ≤256;
wynik musi mieć dokładnie tyle wartości co wejście, wyłącznie skończonych
i nieujemnych. W tym zakresie wyniki pozostają w pamięci.

Prywatny preflight, w skonfigurowanym środowisku procesu z `DATABASE_URL`,
`IMAGE_DIGEST` i pozostałymi settings aplikacji:

```bash
python -m retailops_ai.forecast_jobs.runtime_cli release-check \
  --inputs-dir /private/path/batch-profile-sha256-<64-hex> \
  --release-id model-release-sha256-<64-hex>
```

Release pochodzi wyłącznie z niezależnego audytu PostgreSQL AI,
nie z pliku klienta. Polecenie nie promuje modelu, nie zapisuje runu
ani prognoz. Zwraca liczbę i hash obliczeń oraz czas, bez wartości prognoz
i bez szczegółów poświadczeń w błędach. Polecenie używa teraz
[supervisora z limitami 05.5b](forecast-input-store.md); nie publikuje wyników.

## Odbiór i dalszy zakres

```bash
make forecast-runtime-check
```

To szybka bramka zgodności nowych kontraktów, włączona do `make check`.
Pełny odbiór archiwalnych adapterów wymaga prywatnych danych:

```bash
python scripts/check_forecast_runtime.py \
  --run-dir /private/path/run-<id> \
  --inputs-dir /private/path/batch-profile-sha256-<64-hex> \
  --output reports/forecast-runtime-archive.json
```

Odbiór adaptera ma cel diagnostyczny i oddzielnie raportuje zgodność locków.
Zamrożone archiwum 04.8 ma starszy lock features niż model/runtime;
loader odrzuci takie wejście. Przed odbiorem serving należy przygotować
i zweryfikować spójny pakiet w AI 04, bez przepisywania pinów archiwum.

Rejestr profili i ograniczony supervisor mają [odbiór 05.5b](forecast-input-store.md).
[Integracja z lease i atomowym outputem 05.6](forecast-publication.md) oraz
[odczyt prognoz 05.7a](forecast-read.md) mają lokalny odbiór techniczny.
[Odczyt historycznych ocen 05.7c](evaluations.md) również przeszedł odbiór.
Pozostaje odbiór na zakwalifikowanym modelu AI 04
i pełny source watermark freshness. AI 10 dodaje zdarzenia.
Nie ma zdalnego Required CI tego zakresu.
