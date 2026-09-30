# Korekta jakości forecastingu

Nowa ścieżka `forecast-quality-remediation-1.0.0` zachowuje wszystkie progi
[pierwotnej oceny](forecast-quality.md). Nie nadpisuje wcześniejszego backtestu,
raportu quality ani runu 04.8. Każdy wynik ma osobny content ID i checksumy.
[Kampania v8](../contracts/forecast/v1/quality-remediation.campaign-v8.json) zamraża
źródło, daty i późniejsze development holdouty przed ich oceną. Inventory jest
obecne w źródle 2.7, ale nie jest dodawane do macierzy cech w tym zakresie.

Wstępna kontrola korzysta z tego samego as-of wyboru historii co builder cech.
Liczy wszystkie 14 możliwych horyzontów, także potencjalnie niekwalifikowane.
Jeśli nawet ta górna granica nie osiąga 30 wierszy, kampania zatrzymuje się
przed materializacją cech. Wynik `not_rejected` nie potwierdza gotowości i
wymaga pełnej kontroli cech. Nie buduje etykiet target ani nie ocenia ich
wyników. Weryfikacja integralności i indeks źródła mogą przeglądać późniejsze
obserwacje; historia użyta do liczenia zawsze ogranicza się do daty i cutoffu
bieżącego origin.

Pełna kontrola przed treningiem zlicza wyłącznie cechy znane w origin, bez etykiet
target. Każdy koszyk zero/low/medium/high wymaga co najmniej 30 potencjalnie
kwalifikowanych wierszy w validation i holdoucie każdego folda. Brak próbki
zatrzymuje kampanię przed dopasowaniem modeli. Obecność cech nie gwarantuje
dostępności późniejszych etykiet ani jakości prognozy. Pierwsza
[kampania](../contracts/forecast/v1/quality-remediation.campaign.json) z 12
produktami nie miała koszyków zero i high; jej holdouty nie zostały ocenione.
V2 zwiększa przekrój do 24 produktów i stosuje okna rolling, zachowując progi
jakości oraz limit zasobów modeli. Wstępna kontrola historii źródłowej nadal
nie znalazła koszyka zero. V3 stosuje standardową liczbę 100 produktów profilu
`ai-dev` z jednym sklepem, aby poszerzyć próbkę produktów o przerywanej sprzedaży
w ramach dotychczasowego limitu wierszy treningowych. Seed pozostaje 42.
V4 zachowuje te same parametry danych i przypina producenta z indeksami
pozycji inventory. Kontrolny eksport wykazał identyczną zawartość 58 tabel;
indeksy przyspieszają weryfikację bez zmiany progów, cutoffów i polityki źródła.
V4 przeszła import i curated, ale kontrola cech wykazała brak high oraz
zero w trzech wymaganych oknach. Trening nie został uruchomiony. V5 obejmuje
dwa sklepy, przy tym samym seedzie i 100 produktach; maksymalna liczba
wierszy treningowych nadal mieści się w oryginalnym limicie 50000.
V6 i V7 zachowują parametry V5 oraz tę samą recepturę. Producent V7 indeksuje
oferty dostawców i sprawdza pełną historię bieżącego zamówienia przy dostawie.
Zachowuje globalną unikalność, kontrolę całego nowego batcha zamówień i
końcową kontrolę kompletnej księgi. Cache normalizacji jest ograniczony do
32768 niezmiennych rekordów. Regresja 559 testów i parytet 58 tabel przeszły.
V8 zachowuje pełny source V7 wraz z checksumami 63 plików i niezależnie
weryfikuje go indeksem pozycji/epizodów; 560 testów oraz parytet 58 tabel
potwierdzają zachowanie kontroli. Przebieg nie regeneruje danych.
V8 przyjęła 626238 wierszy bez quarantine, lecz konserwatywna kontrola
historii wykazała 0 próbek zero w validation foldów 1–2 i holdoucie folda 3.
Zakończyła się przed cechami i treningiem, z `not_ready`; szczegóły zawiera
[evidence korekty](evidence/04-quality-remediation.md).
Runner wymaga dokładnego, czystego commita producenta wskazanego w kampanii.

Korekta i wybór zależą wyłącznie od wolumenu znanego w origin oraz kategorii.
Pierwsza połowa originów validation dopasowuje ograniczony współczynnik
średniej sprzedaży rzeczywistej do średniej prognozy. Przy samych zerowych
prognozach używa średniej sprzedaży z tego bloku jako offsetu. Druga połowa
wybiera spośród poprawionych trzech baseline'ów, RF i HGB według MAE. Zmiana
wymaga poprawy >5% względem zamrożonego baseline'u; inaczej zachowuje jego
oryginalne predykcje. Brak 30 próbek w którymkolwiek bloku pozostawia jawne
`not_ready`, nawet jeżeli fallback ma korzystny wynik.

Przedziały wykorzystują dolny i górny quantile reszt ze znakiem drugiej połowy
validation, nominalnie 90%. Kalibracja jest osobna dla wolumenu i kategorii.
Zbyt mała grupa przechodzi do jawnie raportowanego koszyka wolumenu, a następnie
całego folda; każda użyta pula musi mieć co najmniej 50 reszt i osiągalne rangi.
Brak takiej puli daje brak przedziału i blokadę bramki. Granice są nieujemne
i obejmują point forecast. Raport predykcji zachowuje calibration ID i poziom
fallbacku dla każdego ocenianego klucza.

Wszystkie etykiety validation muszą być dostępne do selection cutoff.
Korekta, wybór i kalibracja zostają zamrożone przed development holdoutem.
Validation jest diagnostyką wyboru receptury, nie deklaracją historycznie
wystawionej prognozy z już dopasowaną korektą. Wybór i kalibracja współdzielą
drugi blok; zależność czasu i nakładanie horyzontów wykluczają deklarację
gwarancji pokrycia. Dopiero późniejszy holdout sprawdza rzeczywiste pokrycie.

Każdy model zachowuje te same memberships i przyczyny wyłączenia. Raport
pokazuje poprzedni wybór, zamrożony baseline oraz nową recepturę. Puste koszyki,
zera mianownika, niedostateczna próba, bias, regresje, szerokość i pokrycie
nadal mogą blokować cały wynik. Nie ma wyboru zastępczej metody na holdoucie.

```bash
.venv/bin/python -m retailops_ai.forecasting.cli quality-remediate \
  --feature-dir data/generated/features/<feature_set_id> \
  --backtest-dir data/generated/backtests/<backtest_id>
.venv/bin/python -m retailops_ai.forecasting.cli remediation-verify \
  --remediation-dir data/generated/forecast-remediation/<remediation_id> \
  --feature-dir data/generated/features/<feature_set_id> \
  --backtest-dir data/generated/backtests/<backtest_id>
make forecast-remediation-check
.venv/bin/python scripts/run_forecast_remediation_campaign.py \
  --snapshot-dir /absolute/path/to/frozen/source-2.7-snapshot
```

Exit 3 oznacza kompletny raport `failed/not_ready`; exit 2 błąd wejścia.
Sukces weryfikacji integralności nie oznacza przejścia bramek modelu.
`remediation-verify` ponownie oblicza recepturę, kalibrację, predykcje i bramki
z przypiętych rodziców; przeliczenie hashy sfałszowanego raportu nie wystarcza.
Wynik pozostaje artefaktem rozwojowym. Registry, pełny run treningowy oraz
dopuszczenie serving wymagają osobnego odbioru AI 05. Portfolio final test
nie jest otwierany ani używany do strojenia.
