# AI 04 — rozwój następnej kampanii

Status: **development, `not_ready`**. To przygotowanie po niezaliczonej v11,
nie wynik nowej kwalifikacji. Użytkownik zezwolił na oddzielny, niezależny
zbiór syntetyczny; wcześniejsze źródła, snapshoty, wyniki oraz raport zatrzymanej
v10 pozostają zachowane. Nowe testy nie mogą służyć do strojenia kolejnej receptury.

## Niezmienione wymagania

Obowiązuje zamrożony [protokół jakości 2.0](forecast-quality-v2.md).
MAE ocenia medianę, MSE i bias oceniają średnią; pełna kwalifikacja wymaga obu
celów oraz przedziałów. Szerokość dzielona przez małą średnią sprzedaży jest
diagnostyką. Zerowa sprzedaż nie wymaga sztucznego mianownika. Dodatnia prognoza
na całkowicie zerowej próbce oraz rzeczywiste regresje nadal powodują błędy.

Nie zmieniono progów ani plików przypiętych w freeze v11. Nowe moduły mają osobne
nazwy i nie zmieniają historycznej klasyfikacji **165 passed / 59 failed**.
W szczególności pozostają udokumentowane niedoszacowanie średniej koszyka zero,
regresja mediany Home Improvement oraz regresje MSE i interval score.

## Zmiana metody, rozwijana przed nowym testem

Kandydat zachowuje dokładne wartości mediany i przedziałów wybranego baseline'u.
Korzysta z istniejącej reguły protokołu dopuszczającej zachowanie baseline'u.
Średnia ma osobną, jawną recepturę; poprawka dla historii zerowej jest addytywna,
bez dzielenia przez zerową prognozę i bez przenoszenia dużych korekt zwykłych
produktów do koszyka zero. Warianty i siła wygładzania są porównywane wyłącznie
na development. Raporty zachowują każdy niespełniony warunek.

Kalibracja ma wykorzystywać pełną walidację danego foldu. Wariant używający
train oraz validation sprawdza osobne cutoffy i rozłączność dat celu.
Łączenie kohort zachodzi tylko w obrębie tego samego logicznego foldu:
późniejsza walidacja nie może wpłynąć na wcześniejszą prognozę.
Osobno liczone są klucze prognoz i unikalne zdarzenia sprzedaży; czternaście
horyzontów nie daje czternastu niezależnych obserwacji tego samego dnia.

Rozszerzone porównanie na zachowanej walidacji obejmuje 13 wariantów i **0 wierszy
holdout**. Jedenaście wcześniejszych wyników odtworzono bez różnic. Osobne
wygładzanie kategorii w koszyku zero nie wystarcza, aby uzasadnić dołączenie
starszego train: dwa takie warianty nadal mają **110 passed / 2 failed**.
Dla siły 50 bias zero wynosi −15,67% i +29,39% w pierwszych dwóch foldach;
dla siły 200: −18,46% i +36,09%. Wszystkie te porażki pozostają w raporcie
`reports/ai04-v12-development-category-trainval.json`.
Warianty z samą walidacją zaliczają po 112 warunków kalibracji. Kandydatem
do pilota jest additive 50, zero category 200, validation only;
**wynik kalibracji nie jest kwalifikacją**.

## Przygotowane mechanizmy

- `functional_development.py` buduje niezmienny cache tylko train/validation,
  z kontrolą cutoffów, rodziców, preprocessing i sum kontrolnych. Cache
  `forecast-development-sha256-20ccd01733076ad7ce2c20e530d50cbb26347da0c88f32507f456d28f92c8883`
  ma **0 wierszy holdout** i nie uprawnia do statusu `ready`.
- `functional_v12_quality.py` liczy te same metryki strumieniowo. Porównanie
  wszystkich **214 032** predykcji v11 odtworzyło dokładnie wszystkie **224**
  raporty segmentów, w tym **59** niezaliczonych: **0 różnic**. Dowód:
  `reports/ai04-v12-streaming-parity.json`. Był to replay ujawnionej v11,
  bez otwarcia nowych holdoutów.
- `functional_v12_archive.py` archiwizuje pełne wskazane drzewa i sprawdza
  każdy plik także po dekompresji. Odtworzenie kopiuje zachowane bajty,
  bez uruchamiania generatora. Nie usuwa plików wejściowych.
- `functional_v12_exposure.py` rezerwuje całą listę seedów we wspólnym rejestrze.
  Zmiana katalogu wynikowego nie omija wcześniejszego użycia danych.
  Otwarcie holdoutu jest zapisywane przed oceną; zmiana receptury po otwarciu
  wymaga nowych danych. Kompletność wymaga wszystkich wcześniej zaplanowanych
  kohort, również tych z niekorzystnym wynikiem.
- `functional_v12_inputs.py` i `functional_v12_cohort.py` zachowują pełne
  eligible/excluded/purged counts, cutoffy i identyczne cechy, etykiety oraz
  referencyjne prognozy. Kontrola na starej v11 potwierdziła zgodność ośmiu
  tabel forecast oraz 7644 cech, 546 historii, 12740 etykiet i 22932 membershipów.
  Pełny replay buduje wejścia z zachowanych bajtów snapshotu.
- `functional_v12_campaign.py` najpierw sprawdza wszystkie kohorty i zapisuje
  końcowe receptury. Dopiero potem otwiera holdouty. Średnie kalibruje osobno
  w każdym logicznym foldzie. Niezależny replay używa zapisanych parametrów,
  porównuje wszystkie bajty predykcji i metryk i nie wykonuje fitowania.
  Jawne wznowienie po awarii dopuszcza jedynie identyczne parametry i zachowuje
  poprzedni raport przerwania; nie daje nowej kwalifikacji ujawnionego testu.
- Opcjonalny workflow `ai04-cohort-preparation.yml` przygotowuje źródło,
  snapshot, wejścia i checkpointy, bez oceny jakości holdoutu. Plik wykonania
  pozostaje nieobecny. Każdy artefakt ma limit, pełną weryfikację i czas
  przechowywania jednego dnia. Błąd po publikacji źródła uruchamia zachowanie
  częściowego checkpointu, który nie może zostać zakwalifikowany.
- `functional_v12_run.py` eksportuje pełny, samodzielnie weryfikowalny run:
  kampanię, replay, receptury, predykcje i checkpointy wszystkich kohort.
  Sprawdza liczebności oraz odtwarza ocenę bramek z zapisanych statystyk.
  Zachowuje również niezaliczone wyniki; sama poprawność archiwum nie nadaje
  statusu `ready`.

Polecenia `scripts/run_forecast_functional_v12_campaign.py` nie uruchamiają
generatora. `score --freeze … --registry … --checkpoints … --output …`
ocenia cały zamrożony zestaw. Ten sam zestaw argumentów z `--replay …`
odtwarza wskazaną kampanię, a `--resume …` wznawia zachowany katalog przerwania
z identycznymi parametrami. `export --campaign … --replay … --checkpoints …
--output … --code-commit …` tworzy run; `verify --run …` sprawdza go ponownie.
Pełna kwalifikacja wymaga zarówno poprawnego replay, jak i wszystkich bramek.

Eksport zawiera również `model_card.json`, `signature.json` i `input_example.json`.
Karta wiąże zamrożoną metodę, seedy, rodziców, receptury i ich cutoffy z raportem
wszystkich segmentów. Signature opisuje istniejącą granicę kompaktowego wiersza
wejściowego i osobne wyniki: medianę, średnią oraz przedział. Predykcja nie
potrzebuje etykiety; `actual` i `label_available_at` w przykładzie są `null`.
Przykład jest jawnie syntetycznym przykładem schematu, a nie wierszem nowego
testu ani pomiarem jakości. Eksport nie wykonuje dodatkowych predykcji lub fitów.
Weryfikator sprawdza SHA tych dokumentów oraz odtwarza ich treść z zapisanych
metadanych kampanii. Nie zmienia oceny jakości.

Handoff jest wersjonowany i **nie jest zgodny ze starym importerem jednej
prognozy**. AI 05 potrzebuje adaptera kampanii v12 zachowującego oba cele,
pełny spis kohort, lineage i oryginalne ID/czasy. Dotychczasowy importer
`model_lifecycle.evaluation_importer` oczekuje `forecasting.run` i jakości v1,
a `model_lifecycle.mlflow.load_smoke` wyniku `nonnegative_units`. Nie wolno
przemianować tej kampanii na stary format. MLflow, registry, review promocji
oraz serving pozostają odrębnymi decyzjami AI 05. Niezmierzone czasy treningu,
cold load i latency mają `null`; eksport nie odgaduje ich z timestampów plików.

## Rozmiar i miejsce — plan roboczy, jeszcze nie freeze

W v11 efektywna liczba zdarzeń zero w holdoutach wynosiła około
7,77 / 8,70 / 9,06 na fold, mimo tysięcy powielonych kluczy prognoz.
Dla train i validation razem, z rozłącznymi datami celu, wynosiła
14,81 / 11,62 / 17,75. Oszacowanie liczebności uwzględnia zarówno szum testu,
jak i estymacji średniej; samo powielenie 80 krótkich kohort daje zbyt mały
margines dla jednoczesnej oceny trzech foldów.

Rozważany plan to **64 niezależne seedy**, po **232 dni**, z oknami
train/validation/holdout po **28 dni**, trzema foldami rolling, krokiem 28 dni,
purge 15 dni i dojrzałością etykiet 15 dni. Pozostają wszystkie horyzonty 1–14,
kategorie, kanały oraz koszyki wolumenu. Daty okien wynikają z kalendarza,
nie z przyszłych wyników. Nie wolno usuwać słabszych seedów ani dobierać
liczebności po obejrzeniu testu. Przybliżenie planistyczne nie gwarantuje
zaliczenia bramek i samo nie jest nową bramką jakości.

Pomiar archiwizacji już zachowanej v11: raw source, kwalifikacja, snapshot
i kampania zajmują **99 649 404 B** po kompresji, **263 pliki**;
pełna weryfikacja zapisu trwała 8,50 s. To odrębny checkpoint rozwojowy
`functional-checkpoint-sha256-78777a2ef2a040943c8f3ba00131aa32bff8957e5a70851f364683f7327a20e2`.
SHA archiwum: `0e2dacf7dcf24056405349f9c807af373677dc5f204b5014ccfee6455d21e41e`.

Liniowe oszacowanie 64 dłuższych kohort daje około **8,5 GiB** archiwów,
przed uwzględnieniem nowych kompaktowych wejść. Wcześniejszy szkic zakładał
dodatkowe 16 GiB rezerwy. Przed freeze zastąpi go jawny plan faz wykonania
oparty na pomiarze pilota: wszystkie checkpointy i predykcje, odtworzenie
jednej kohorty, indeks deduplikacji, pobierany ZIP, import, pojedyncza kopia
predykcji replay oraz zapas. `functional_v12_resources.py` sprawdza ten budżet
osobno dla rzeczywistych wolumenów. Brak miejsca daje raport blokady; nie
powoduje zmniejszenia próby ani usunięcia danych. To limity zasobów, a progi
jakości pozostają bez zmian.

**64 kohorty są roboczym planem operacyjnym, bez obietnicy 95% mocy.**
Oszacowanie musi uwzględniać wariancję zarówno walidacji, jak i testu.
Minimum liczby zdarzeń z jednej starej realizacji jest scenariuszem
konserwatywnym; średnia trzech różnych okien też nie jest estymacją z wielu
niezależnych seedów. Foldy współdzielą część zdarzeń, więc ich prawdopodobieństw
zaliczenia nie wolno mnożyć tak, jakby były niezależne. Liczebność zostanie
ustalona przed nową oceną, bez dobierania seedów lub powiększania próby po
obejrzeniu wyniku. Ten dokument nie stanowi zgody preflight na kampanię.

Osobny seed **710001** służy wyłącznie do testów zgodności i pilota wydajności.
Jest wykluczony z przyszłej kwalifikacji. Rozważane seedy testowe
**720001–720064** nie zostały jeszcze użyte. Receptura, lista seedów, okna,
kod, wymagane segmenty i zasady oceny muszą zostać zamrożone przed oceną
nowych danych. Pełna kwalifikacja wymaga następnie wszystkich bramek,
niezależnego replay, kompletnego archiwum oraz odbioru kodu i CI.
