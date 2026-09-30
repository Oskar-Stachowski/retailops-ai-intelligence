# Korekta jakości forecastingu

Nowa ścieżka `forecast-quality-remediation-1.0.0` zachowuje wszystkie progi
[pierwotnej oceny](forecast-quality.md). Nie nadpisuje wcześniejszego backtestu,
raportu quality ani runu 04.8. Każdy wynik ma osobny content ID i checksumy.
[Kampania v2](../contracts/forecast/v1/quality-remediation.campaign-v2.json) zamraża
źródło, daty i późniejsze development holdouty przed ich oceną. Inventory jest
obecne w źródle 2.7, ale nie jest dodawane do macierzy cech w tym zakresie.

Kontrola przed treningiem zlicza wyłącznie cechy znane w origin, bez etykiet
target. Każdy koszyk zero/low/medium/high wymaga co najmniej 30 potencjalnie
kwalifikowanych wierszy w validation i holdoucie każdego folda. Brak próbki
zatrzymuje kampanię przed dopasowaniem modeli. Obecność cech nie gwarantuje
dostępności późniejszych etykiet ani jakości prognozy. Pierwsza
[kampania](../contracts/forecast/v1/quality-remediation.campaign.json) z 12
produktami nie miała koszyków zero i high; jej holdouty nie zostały ocenione.
V2 zwiększa przekrój do 24 produktów i stosuje okna rolling, zachowując progi
jakości oraz limit zasobów modeli.

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
