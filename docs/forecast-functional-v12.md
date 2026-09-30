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
przed uwzględnieniem nowych kompaktowych wejść. Obowiązuje również **16 GiB**
rezerwy na materiały robocze, indeks, odtworzenie i zapas.
Pełne uruchomienie wymaga ponownego pomiaru miejsca oraz rzeczywistego rozmiaru
i czasu pilota; ten dokument nie stanowi zgody preflight na kampanię.

Osobny seed **710001** służy wyłącznie do testów zgodności i pilota wydajności.
Jest wykluczony z przyszłej kwalifikacji. Rozważane seedy testowe
**720001–720064** nie zostały jeszcze użyte. Receptura, lista seedów, okna,
kod, wymagane segmenty i zasady oceny muszą zostać zamrożone przed oceną
nowych danych. Pełna kwalifikacja wymaga następnie wszystkich bramek,
niezależnego replay, kompletnego archiwum oraz odbioru kodu i CI.
