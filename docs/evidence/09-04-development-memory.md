# AI 09.4 — pamięć wspólnego benchmarku development

[Runbook](../forecast-development-comparison.md) i
[receipt JSON](09-04-development-memory.json) opisują zmniejszenie pamięci
na tej samej próbce po AI 06. AI 09 pozostaje **in_progress / not_ready**.

## Sprawdzenie sesji AI 08

Przed zmianą odczytano wyłącznie kod i dowody AI 08 z `d083e0a`.
Stockout ma już streamed curated/private facts, jednorazowy SQLite,
cache jednej fizycznej serii, indeks historii oraz partycje etykiet.
Odbiór etykiet podaje medianę 112.73 → 102.48 MiB; większy pipeline
nie był w tym dowodzie zakwalifikowany. Nowy upstream reader był rozwijany
w tamtej sesji i nie został potraktowany jako przyjęty wynik.

AI 09 korzysta z istniejącego indeksu i typed Parquet forecasting.
Nie kopiuje czytnika stockout ani jego grain produkt × fizyczny magazyn.
Zmiana dotyczy pełnego produktu × selling location × kanału × origin × horizon.
Sesje, ich branche, dane i usługi nie były zmieniane przez AI 09.

## Zmiana i zgodność

Równe frozen `Reference` i `InputValue` współdzielą pamięć tylko w obrębie
jednego pełnego origin. Cała zawartość, także available_at i rekord checksum,
uczestniczy w porównaniu. Typ liczby jest przypięty przez kontrakt cechy;
dodatkowa reprezentacja float zachowuje także różnicę `0.0` i `-0.0`.
Cache kończy się przy zmianie dowolnego elementu serii
lub origin; historia jest dekodowana raz. Nie usuwa się pól ani lineage.
Weryfikacja rodziców, maturity, cutoff, role i populacja zachowują swoje bramki.

TensorFlow reload działa w osobnym, kończącym się procesie CPU, zanim zostaną
wczytane duże drzewa do oceny. Otrzymuje
sprawdzoną float32 matrix z identycznym train-only normalizerem. Worker sprawdza
model bundle, lock, code binding, podpis i model ID, a comparator sprawdza
checksum oraz dokładny shape/dtype/finite/nonnegative output. Budżet workera
pozostaje 1200 s wall/CPU i 1 GiB RSS, sampler co 50 ms; terminacja obejmuje
wyłącznie własne drzewo. Scratch jest prywatny i usuwany także po błędzie.

Hash pełnej populacji train/validation jest teraz strumieniowy. Ma dokładnie
te same bajty canonical JSON array i kolejność, bez listy wszystkich row dumps.
Ten przyrost zmienia code binding przygotowania modelu; stare artefakty
odtwarza przypięty w nich kod, bez wyłączania sprawdzania zgodności.
Architektura, hiperparametry, seedy, encoder, target i progi nie zostały zmienione.
Powtórki inżynieryjne nie wybierają modelu na podstawie jakości.

Nowa próba zachowuje **20 160 identycznych bajtowo prognoz**: sześć wariantów,
po 3360 kluczy validation, w tym 3040 eligible. Wszystkie globalne i segmentowe
metryki są identyczne z 09.3. TF nadal ma 2.51% poprawy MAE mediany wobec
history28, poniżej wymaganego 5%, oraz brak osobnej kalibracji przedziałów.
Validation użyta do early stopping pozostaje diagnostyką development.

## Pomiar i zachowane próby

Historyczna ukończona próba 09.3 miała **1300.36 MiB** peak RSS i osobny limit
1.5 GiB. Jej dwie przerwane próby 1 GiB pozostają zachowane. Przyrost 09.4
ma własny z góry zapisany limit całego drzewa **1 GiB / 1200 s wall/CPU**.

Samo współdzielenie danych nie wystarczyło: próba 4 zakończyła się przerwaniem
przy około 1027.88 MiB. Po izolacji reload próba 5 ukończyła się przy 969.38 MiB,
ale powtórka 6 przekroczyła limit podczas treningu przy 1028.28 MiB.
Wszystkie katalogi, protokoły, trial journals i external interruption receipts
pozostały zachowane; przerwane próby nie mają completed manifest.

Po strumieniowym hashowaniu próba 7 przeszła z 871.27 MiB, ale jej replay
przekroczył 1 GiB przy 1025.23 MiB. Samo dodatkowe GC przed workerem w próbie 8
nie zaliczyło budżetu: przerwano ją przy 1027.16 MiB. Te receipts również
pozostają zachowane. Replay nie zmieniał ukończonego artefaktu próby 7.

Po zmianie kolejności TF/drzew próba 9 przeszła z 869.89 MiB, a jej native/wheel
replay z 858.39/891.53 MiB. Przegląd wykrył jednak poprawny przypadek signed zero,
który porównanie modeli mogło scalić mimo różnych bajtów wejścia. Dwa testy
odtworzyły błąd. Klucz pełnego JSON w próbie 10 przekroczył budżet przy
1024.91 MiB; jej przerwany artefakt i receipt pozostały zachowane.
Końcowy klucz zachowuje pełne porównanie modelu oraz reprezentację float.

Końcowa próba **11** przeszła z **939.02 MiB** RSS, 121.49 s wall i 114.54 s
obserwowanego CPU drzewa: około **27.8% mniej RAM** niż 09.3. Jej wszystkie
wejścia treningu i raw outputs są identyczne także bajtowo, łącznie z typem,
kształtem i znakiem zera. Wcześniejsze próby były wykonywane przy osobnym
CI AI 08; podczas próby 7 wykonano też jednosekundową diagnostykę stosu własnego
procesu. Ciężki proces AI 08 zakończył się przed próbą 11. Nie kontrolowano
pozostałej aktywności komputera ani cache systemu. Czas 121.49 s jest wyższy
niż historyczne 113.24 s; brak pomiaru izolującego wpływ zmiany algorytmu
i warunków komputera. Nie deklaruje się przyspieszenia.
Proces miał nice=15 i pojedyncze skonfigurowane wątki.
Sampler sumuje jednoczesny RSS własnych potomków co 50 ms, bez samplera.
Nie mierzy całego komputera, większego profilu ani wszystkich tymczasowych baz.

## Kontrole i granice

Ukierunkowane testy mają **58/58 zaliczeń w 11.31 s**. Obejmują zgodność całych
wierszy i normalizera z dotychczasowym readerem, frozen storage, każdy element
grain, collision/content/availability, oba porządki signed zero, kolejność digestu,
zmienioną populację/role/budżet macierzy, nieprawidłowy output workera,
niebezpieczny broadcast shape oraz rzeczywistą terminację własnego workera
przy małym limicie RSS/wall. Rzeczywisty test CPU porównuje nowy worker
z dotychczasowym `LoadedChallenger.predict` i replay bez refitów.

Natywny replay końcowej próby bez refitów przeszedł przy **579.42 MiB** RSS
i 101.21 s wall. Końcowy odłączony wheel odtworzył te same wyniki przy
**577.00 MiB** i 96.54 s wall, z katalogu poza repozytorium. Wszystkie jego
moduły pochodziły z zainstalowanego pakietu; comparator nie importował TensorFlow.
264 moduły Python wheel są identyczne ze źródłami. Limity pozostają
1 GiB / 1200 s wall/CPU. Pełne `make ci-local` zakończyło się poprawnie
w **2012.82 s**: **1839/1839** testów głównych w 1721.65 s oraz **3/3**
rzeczywiste testy CPU TensorFlow w 61.27 s, bez pominięć. Przeszły lint,
formatowanie 568 plików, mypy 339 plików źródłowych, wszystkie kontrolne
skrypty i kontrakty, budowa sdist/wheel, wyłącznie konfiguracja Compose
oraz oba skany gitleaks. Pakiet z pełnego CI jest identyczny z odtworzonym
wheel, także SHA-256 i wszystkie jego pliki. Lokalne CI nie uruchamiało
usług innych sesji; odbiór trwałych usług jest odrębnym jobem Required CI.
Własny częściowy przebieg CI sprzed poprawki signed zero zatrzymano wyłącznie
w jego drzewie procesów; zachowano log i receipt, bez liczenia go jako zaliczenie.

Poprzedni [Required CI dla `7d36034`](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37194024255)
został anulowany; nie jest zaliczeniem
tego przyrostu. Log joba checks pokazuje przejście głównych kontroli i pakietu,
a następnie anulowanie podczas końcowych testów TensorFlow po około 45 minutach.
To wskazuje na wyczerpanie limitu joba. Limit tylko joba checks wzrasta
z 45 do 60 minut, bez usuwania bramek, ignorowania błędów ani zmiany budżetów
modeli. Nowy commit wymaga własnego Required CI.

Odczyt nadal materializuje do 3000 okien na rolę, matrix ma dotychczasowy
limit 64 MiB. Wynik tej małej próbki nie kwalifikuje `ai-dev`/`ai-training`
ani nie gwarantuje RSS dla innych danych. Osobna kalibracja i niezależna ocena,
większy reader/batch training, globalny trial/access journal, trzy seedy,
scenariusze, niepewność, końcowe AI 07/08 policies i lifecycle pozostają otwarte.
Final test portfolio nie został otwarty, model nie został promowany.
