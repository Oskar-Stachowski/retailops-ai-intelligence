# AI 09 — wspólne porównanie prognoz development

Przyrost 09.3 porównuje trzy empiryczne baseline'y, RF mean, HGB mean/median
i TensorFlow mean/median na tych samych kluczach. Przed pierwszym fitem zapisuje
konfiguracje, source/curated/features/split IDs, fold, seedy, kod, środowisko
oraz stały reference `history28`. Każda rodzina ma jedną konfigurację.
Nie wybiera zwycięzcy ani nowego baseline'u na podstawie wyniku.

Obecny adapter wymaga zweryfikowanego curated 1.1 / source 2.7, czyli danych
z natywnym ledgerem po AI 06. Weryfikuje zgodność rodzica i parametrów z
features; odrzuca inne source IDs, seedy i nieobsługiwaną feature policy.
Trening używa tylko eligible train, a ocena zachowuje wszystkie klucze validation,
w tym wyłączone punkty z null predictions. RF mean nie staje się medianą.
HGB i TensorFlow nie otrzymują sztucznych przedziałów.

Walidacja jest również użyta do early stopping TensorFlow. Jest to jawna
diagnostyka development, **nie niezależny dowód jakości**. Weryfikacja istniejącego
splitu odtwarza również wcześniejsze etykiety development holdout; benchmark
nie używa tej roli do uczenia, wyboru ani metryk i nie nazywa jej nietkniętą.
Format splitu nie zawiera final testu portfolio.

## Wynik i odtwarzanie

`protocol.json` powstaje przed fitami. `trials.jsonl` zapisuje ich rozpoczęcie,
zakończenie, model IDs i awarie, z flush/fsync. Jest to rejestr jednej próby;
globalny rejestr kampanii pozostaje otwarty. RF/HGB zapisują typed portable JSON,
TensorFlow supported MLflow Keras bundle. Każda próba wymaga nowego prywatnego
katalogu; nieudana zostaje zachowana i retry nie nadpisuje jej danych.

`predictions.jsonl` zawiera pełną populację dla każdego modelu. `report.json`
korzysta z istniejącego evaluator v2: globalnie, horyzonty 1–14, kategorie,
kanały i wszystkie wymagane koszyki wolumenu. Kategorie/kanały z train także
pozostają widoczne, jeśli validation ich nie zawiera. Puste segmenty mają
`not_ready`, all-zero WAPE pozostaje null, a mierzalne porażki zostają w raporcie.
Przedziały empirycznych baseline'ów są surowe, bez osobnej kalibracji.

Manifest wiąże checksums wszystkich zapisanych plików. `verify-comparison`
ponownie weryfikuje rodziców i środowisko, train preprocessing, label digest,
budżet fitów drzew i TensorFlow bundle. Ładuje modele, odtwarza wszystkie
predykcje i raport bez nowych fitów. Inny kod lub platforma wymaga własnego
odbioru; nie ma deklaracji identycznych bajtów między macOS i Linux.

```sh
uv run --locked --project environments/tensorflow \
  python -m retailops_ai.tensorflow_challenger.cli compare-development \
  --curated /path/to/curated-sha256-ID \
  --features /path/to/features-sha256-ID --split /path/to/split-sha256-ID \
  --fold development-v1 --output /private/tmp/ai09-new-comparison

uv run --locked --project environments/tensorflow \
  python -m retailops_ai.tensorflow_challenger.cli verify-comparison \
  --curated /path/to/curated-sha256-ID \
  --features /path/to/features-sha256-ID --split /path/to/split-sha256-ID \
  --output /private/tmp/ai09-new-comparison
```

Opcjonalny `--policy` przy uruchomieniu przyjmuje
[zamrożoną recepturę development](../contracts/evaluation/v2/development_comparison.default.json).
Budżet modeli drzew pozostaje 120 s wall / 90 s CPU / 1 GiB RSS na fit,
TensorFlow 1200 s wall/CPU / 1 GiB RSS. Wątki są pojedyncze i fity sekwencyjne.
Odczyt nadal ma limit 3000 windows na rolę, a TF matrix 64 MiB;
większe dane są odrzucane. Cały zapis ma limit 128 MiB.
Receipt zasobów modeli nie jest automatycznie pomiarem peak RSS całego pipeline.
Osobny sampler dla rzeczywistej próby obejmuje drzewo procesu i ma własny budżet.

## Pamięć porównania — przyrost 09.4

Przed zmianą sprawdzono kod i odbiór AI 08 z `d083e0a`: stockout ma już
strumieniowy odczyt curated, prywatny SQLite, cache fizycznej serii, indeks
historii i partycje etykiet. Jego upstream reader był dalej rozwijany w osobnej
sesji. Forecasting używa innego grain: produkt × selling location × kanał ×
origin × horizon. Porównanie korzysta z istniejącego zweryfikowanego indeksu
forecasting i odczytu Parquet; nie dodaje kopii czytnika stockout.

Nowy adapter współdzieli równe, frozen `Reference` i `InputValue` pomiędzy
horyzontami jednego pełnego origin oraz dekoduje jego historię raz. Kluczem
jest cała zawartość typed obiektu, także czas dostępności i checksum rekordu;
kolizja hash nie omija porównania wszystkich pól. Reprezentacja float
zachowuje dodatkowo signed zero przy typie przypiętym przez kontrakt cechy.
Cache jest czyszczony przy
zmianie produktu, lokalizacji, kanału lub origin. Pełne pola, lineage, maturity,
chronologia, parent verification i populacja pozostają sprawdzane.

Hash pełnej populacji TF jest liczony wiersz po wierszu, z zachowaniem bajtów
dotychczasowej canonical JSON array i jej kolejności. Trening i odczyt nie
budują już listy wszystkich zserializowanych cech na potrzeby tego hasha.
Zmiana kodu przygotowania ma nowy code binding; stare modele wymagają
przypiętego w swoim manifeście kodu, bez obchodzenia kontroli zgodności.

TensorFlow reload i predykcja działają w nowym procesie CPU, który kończy się
przed wczytaniem dużego portable forest i pozostałą oceną. Proces porównania
przygotowuje macierz float32 na dysku
z tym samym train-only normalizerem i wiąże pełną populację validation z modelem.
Worker ponownie sprawdza bundle, lock, kod, podpis i model ID; zwraca sprawdzone
wyniki w batchach tej samej wielkości. Supervisor mierzy własne drzewo co 50 ms,
stosuje dotychczasowe limity TensorFlow i sprząta tylko własny proces/scratch.
Receipt obejmuje także cold load z importem frameworka, nie tylko ciepły loader.

Odczyt nadal materializuje do 3000 okien na rolę. To ograniczenie pamięci małej
próbki, nie odbiór większego readera ani `ai-dev`/`ai-training`.
[Dowód 09.4](evidence/09-04-development-memory.md) zapisuje pomiary, nieudane
próby oraz zgodność predykcji i metryk. Historyczne artefakty 09.3 odtwarza
kod z przypiętego w nich commitu; nowy kod ma własny comparison ID.

## Pozostały zakres

Ten benchmark nie kwalifikuje `ai-dev` ani `ai-training`. Osobne calibration
i evaluation partitions, większy bounded reader/batch training, globalny audyt
prób i dostępu do final testu, scenariusze/seedy, niepewność oraz końcowe
polityki AI 07/08 i trzy karty modeli nadal są wymagane. Każdy wynik procesu
zachowuje `deployment_status=not_ready` i `promotion_allowed=false`.
