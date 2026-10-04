# 09.2 — kompaktowy Keras CPU i wspólny kontrakt

Stan przyrostu: **wszystkie wymagane lokalne bramki zaliczone**. [Receipt](09-02-tensorflow-challenger.json)
zachowuje model identity, pełne checksums bundle, pomiary oraz mierzalne porażki.
[Runbook](../tensorflow-challenger.md) podaje architekturę, granice i polecenia.
Poprzedni PR receipt 09.1 zachowuje historyczny zakres; jego Required CI zakończyło
się sukcesem dla dokładnego `bbbe2207776c11846d2c6fef7b198ddcdc119ae3`.

## Wykonano

- Rzeczywisty Keras 3.11.3 / TensorFlow 2.20.0 CPU z osobnym locked environment;
  główny lock ma nadal SHA-256 `33c53d1a1f08d5c90b3b61c79e6aeb732f0eebc6e8be36e735c93ca277492587`.
- Okno 28 dni, train-only imputing/vocabulary/scaling i osobne direct 1–14
  mean/MSE oraz median/MAE; maski nie obcinają niepełnych grup horyzontów.
- Wspólny grain i istniejący v2 evaluator; zmienione przyszłe obserwacje nie
  wpływają na historyczne wejście. Holdout/final role jest odrzucana przed odczytem.
- Supported MLflow Keras artifact z normalization, policy, pełnym lockiem,
  signature i checksums; lokalny izolowany run MLflow, bez zmiany aliasów/runtime.
- CPU reload po zapisie, w świeżym procesie i powtarzalne numerycznie ponowienie
  przy tym samym init seedzie; tolerancja rtol/atol 1e-6.
- Blokady CPU/wall/RSS, zachowany failed attempt, brak nadpisania; zmieniony
  binary/preprocessor i przeliczony manifest z niezgodnym podpisem blokują reload.
- 71 targeted checks przeszło w 37,03 s. Obejmują także cały kontrakt 09.1,
  rzeczywisty trening i CLI na weryfikowanych disk parent artifacts.

Kontrolna próba ma **jeden train window, jeden validation window i 14 eligible
validation keys**; trzy epoki służą regresji. Nie jest odbiorem standardowego
profilu AI ani niezależną oceną portfolio. Przykładowy retained bundle ma
301 944 bajty; worker z importem/saving/reload trwał 7,65 s i osiągnął około
577 MiB RSS. Sam training trwał 0,071 s, cold load już zaimportowanego flavor
0,014 s, batch inference 0,0013 s. Te koszty nie są projekcją pełnej skali.
Świeży installed-wheel reload jest osobnym pomiarem w receipt.

Diagnostic względem history28 zachowuje `not_ready`: próba jest za mała,
brakuje skalibrowanych przedziałów, a median MAE improvement i mean MSE mają
mierzalne porażki. Trening zakończony sukcesem nie kwalifikuje promocji.

## Pozostaje

Osobna kalibracja przedziałów, larger-profile streaming training, qualified
source/curated/features po 06 i final evidence AI 07/08, fair RF/HGB comparison,
zatwierdzony final protocol i audyt prób/test access, trzy data seeds/scenariusze,
segment gates, model cards i decyzje lifecycle. Final testu portfolio nie otwarto,
modelu nie promowano. Pełny lokalny i dokładny zdalny odbiór nowego SHA są
uzupełniane po zakończeniu kontroli, bez nadpisania wyników odrzucenia.

## Pełna regresja i skan

`make ci-local` wykonało lint/format, strict mypy (334 sources), **1799 testów
w 1718,25 s**, wszystkie dotychczasowe checkery, nowe contracts, wheel/sdist,
Compose config oraz **2 rzeczywiste testy TensorFlow w 43,28 s**. Żadna z tych
bramek nie została pominięta ani osłabiona. Końcowy directory secret scan
zwrócił 1 false positive: `forecast_keys_sha256` w evidence zawiera digest
populacji prognoz, bez credential. W receipt nazwano to samo pole
`forecast_population_sha256`; oryginalne pole runtime i mapping są zachowane.
Nie dodano wyjątku do skanera. `make docs-check secrets` po tej zmianie
zwróciło **0**, ze skanem historii i katalogu bez znalezisk. Poprzedni exit=2
i powód pozostają w JSON receipt. Powtarzano tylko poprawione bramki docs/scan;
nie uruchamiano ponownie już zaliczonych 1799 testów.

Fresh installed-wheel CPU reload poza checkoutem przeszedł w 6,40 s;
wynik ma shape `[1,14,2]`, rtol/atol 1e-6. Byte hash finalnego wheel
i ścieżka użytego zainstalowanego modułu są zapisane w receipt.
Dokładny nowy SHA musi jeszcze przejść zdalne Required CI, w tym Linux CPU.
