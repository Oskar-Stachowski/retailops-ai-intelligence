# AI 08.5 — karta i wyjaśnienia development

Przyrost dodaje [kartę modelu](../reference/stockout-model-card.md)
z tabelami LR, permutation importance na tune i lokalnymi faktami PIT.
[Projekt większego profilu](../reference/stockout-profile-resources.md)
zapisuje zmierzone ograniczenia oraz proponowaną partycjonowaną ścieżkę.

Baza to `4a7a76f813eb98b52d23d25742ef567d707eb284`.
Nie generowano nowych danych; źródło temporal smoke, pięć artefaktów
przygotowania oraz porównanie modeli z 08.4 pozostają niezmienione.
Nie oceniano final testu, nie zmieniano wyboru provisional, progów ani runtime.
[Wersjonowany zapis](08-05-stockout-card.json) przypina model/card IDs,
implementację, pomiary natywne/wheel, klasy zakresu i projekt zasobów.

## Wyjaśnienia na tune

Wybrany provisional HGB bez upstream ma nadal AP 0,994294 i Brier 0,029300.
Nie zmieniono modelu, kalibratora ani wyboru. Karta zawiera trzy pełne
tabele współczynników LR, 20 grup permutation importance oraz faktyczny
kontekst 135 punktów tune. Każda grupa ma pięć wspólnych permutacji,
seed 42; wartości i ich wskaźniki braku oraz całe kategorie są przemieszczane
razem. Prawdopodobieństwa tune pozostają surowe, bez późniejszego sigmoid.

| Grupa | Średni spadek AP po przetasowaniu |
|---|---:|
| Średnia zaobserwowana sprzedaż | 0,232722 |
| Kategoria produktu | 0,008640 |
| Days of supply z dni in-stock | 0,007638 |
| Odchylenie sprzedaży | 0,005150 |
| Liczba dni in-stock w historii | 0,003336 |

Największy spadek pokazuje zależność od zaobserwowanej sprzedaży na tej
próbce. Nie dowodzi przyczyny przyszłego braku, niezależnego wpływu cechy
ani przewagi na większym profilu. Przetasowanie skorelowanych wejść może
tworzyć nierealne kombinacje. Odchylenie powtórzeń nie jest przedziałem
ufności; tune było już użyte do wyboru modelu. Nie zmieniono na tej podstawie
cech ani parametrów. Wszystkie ograniczenia pozostają w karcie.

Factual codes opisują znany zapas/wiek snapshotu, pokrycie zapasu sprzedażą,
znane dostawy/zaległe zamówienia i historyczne ograniczenia lub brak wiedzy.
Nie przypisują tym faktom lokalnego wpływu na score HGB; nie nadają
probability ani pasma ryzyka. Testy odrzucają istniejący stockout,
insufficient data i stary snapshot. Brak danych nie staje się zerem.

## Kontrole wykonania

Regresja explanation/training/split ma **49/49 testów w 9,80 s**, w tym
18 nowych przypadków. Obejmuje grupowanie permutacji, wykrywanie sygnału,
powtarzalność, brak wpływu późniejszego sigmoid, właściwą skalę wag,
fakty PIT, ochronę final outcomes i odrzucenie zmienionego rodzica.
W sandboxie joblib zgłosił niedostępny odczyt fizycznych rdzeni i użył
liczby logicznych; recipe nadal ogranicza trening do jednego wątku.

Natywny build/rebuild/verify dał identyczne ID i bajty w
**118,43 / 130,40 / 135,19 s**, z pełnym replay rodziców.
Artefakt ma 256 528 bajtów i tryb 0600. Jego ID:
`stockout-card-sha256-09addf3fdde943a93fd1432ec1a74d1532b19622ee09a2753567104344aac38a`.

Odłączony wheel ze sdist, zainstalowany bez zależności do osobnego katalogu,
odtworzył identyczną kartę spoza repo, przy niedostępnym pakiecie producenta
`data`. Build/verify: **134,28 / 136,55 s**. Wszystkie 278 wejściowych
plików snapshotów/curated/przygotowania oraz modelu pozostały niezmienione.
Training/preparation identity i wybrany model ID są identyczne z rodzicem.
Maksimum RSS pojedynczego dziecka wheel wyniosło 303 923 200 bajtów,
około 289,8 MiB. To `RUSAGE_CHILDREN`, nie jednoczesne RSS całego drzewa.

Rzeczywiste polecenia używały `python -m retailops_ai.stockout_explanation.cli
build` oraz `verify`, tych samych niezmienionych rodziców 08.3/08.4,
modelu `development-sha256-18e71a00...` i `--allow-evaluation-truth`.
Pełną składnię podaje [kontrakt](../reference/stockout-model-card.md).
Natywny `PYTHONPATH` wskazywał `src`, wheel osobny katalog instalacji;
wykorzystano istniejący Python 3.11.15 bez zmiany środowiska AI 05.

Celowo zmieniona wartość importance, po przeliczeniu content hash i card ID,
została odrzucona przez verify w 129,83 s. Osobno zmieniona waga LR, po
przeliczeniu model ID i całego manifestu rodzica, została odrzucona w
127,02 s; nowa karta nie została opublikowana. Obie próby zachowały
niezmienione oryginalne wejścia i wyjścia. Bez jawnej zgody na weryfikację
prywatnego źródła CLI odrzuca działanie przed odczytem rodziców.
Natywne maksimum pojedynczego dziecka wyniosło 324 567 040 bajtów,
około 309,5 MiB, z tym samym ograniczeniem interpretacji RSS.

Pełna regresja z `make ci-local` przeszła: **1853/1853 testów w 1859,67 s**,
bez ostrzeżeń. Lint/format obejmował 576 plików, mypy 344 moduły;
wszystkie 21 targetów `check`, pakiet i Compose config przeszły.
Pierwszy przebieg zakończył się exit 2 na skanie katalogu: skaner błędnie
uznał fragment polskiego zdania w planie zasobów za klucz API. Zmieniono
wyłącznie sformułowanie dokumentacji, bez wyjątku lub zmiany skanera.
Ponowny `make docs-check secrets` zakończył się exit 0, z poprawnymi linkami
i brakiem trafień w historii Git oraz katalogu. Testowany kod pozostał
niezmieniony; nie powtarzano całej regresji po korekcie samego zdania.
Wersjonowany receipt rozróżnia pierwszy exit i końcowe zaliczenie bramek.
Baza modeli `4a7a76f` ma zielone Required CI PR i push
([PR run](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37109266353),
[push run](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37109263232)).
Nowy commit karty będzie wymagał własnego CI.

## Większa próba

Inwentaryzacja plików jest tylko odczytem. Obecne cechy mają 10 249 881
bajtów dla 1632 punktów. Liniowy szacunek dla `ai-dev` to 687 721 795
bajtów, dla `ai-training` 3 667 849 574, przy obecnym formacie.
To rozmiar jednego pliku, bez prognozy całego czasu, pamięci lub generatora.
Aktualne limity 16 MiB i 10 000 punktów oraz wielokrotne skanowanie faktów
wymagają partycji i indeksów PIT. Nie zmieniono limitów i nie uruchomiono
większego profilu.

Projekt opisuje zgodność wszystkich starych punktów, niezależne manifesty
partycji, bounds zapisów, kontrolę complete/replay i pomiar całego procesu.
Budżet pilota 5 GiB scratch / 1 GiB RSS drzewa / 10 min jest propozycją
przed startem, bez deklaracji zaliczenia pełnego `ai-training`.

Pięć z ośmiu kategorii tune nadal ma ranking `not_evaluable`.
Calibrated metrics pozostają diagnostyką in-sample. Final test nie jest
oceniony, threshold policy niezatwierdzona, model niepromowany.
Cały AI 08 nie jest ready; dalszy zakres to przygotowanie większego profilu,
niezależna zamrożona ocena oraz lifecycle/batch/read API.
