# Wspólne wykonanie development 25 → 50 → 100

`scripts/run_ai09_workflow.py` łączy [przygotowanie Source](ai09-source-preparation-execution.md)
z [oryginalnymi wykonawcami prognoz](ai09-development-execution.md).
Przed pierwszą generacją zapisuje cały program: podział dat, producenta,
polityki, sześć konfiguracji RF/HGB/TensorFlow, środowisko workerów oraz
budżety wszystkich trzech skal. Nie trzeba ręcznie przekazywać wyników
między osobnymi dziennikami.

Kolejność obejmuje **43 oryginalne operacje**:

1. Przygotowanie ordinary/demand/physical dla 25 produktów: pięć operacji.
2. Trzy eksporty, sześć fitów, dwa porównania Tune i selekcja: dwanaście operacji.
3. Przygotowanie 50 produktów: pięć operacji.
4. Trzy eksporty, trzy fity wcześniejszych zwycięzców, scoring i selekcja: osiem operacji.
5. Przygotowanie pełnych 100 produktów: pięć operacji.
6. Trzy eksporty, trzy fity tych samych zwycięzców, scoring i selekcja: osiem operacji.

To łącznie **12 fitów**. Każda skala zachowuje pełną historię i wszystkie
przypisane jej klucze obserwacji. Wykonawca nie zmienia wcześniejszego
rankingu, hiperparametrów, preprocessingu ani seeda inicjalizacji.
Niepoprawny wynik rodzin na 50 produktach blokuje generację 100 produktów.

Wymagany prywatny plik `DevelopmentWorkflowOptions` ma schemat
[v40](../contracts/evaluation/v40/development_workflow_options.schema.json).
Zawiera opcje Source dla początkowych 25 produktów; kolejne skale zmieniają
wyłącznie liczbę produktów. Limity generacji, planowania i ponownego
sprawdzania rodziców muszą być takie same, zgodnie z istniejącym kontraktem
przejścia między skalami. Osobne budżety fitu i scoringu podaje się dla
25, 50 i 100 produktów. Nadal obowiązują sufity 12 GiB RSS, 8 GiB scratch,
1200 sekund dla fitu/scoringu oraz rezerwy 1 GiB RAM i 6 GiB dysku.

Sześć uporządkowanych receptur obejmuje dwie różne konfiguracje RF, dwie HGB
i dwie TensorFlow. Wszystkie rodziny używają wspólnego, przypiętego środowiska
TensorFlow; jego skrót musi odpowiadać rzeczywistemu plikowi zależności.
Oryginalne workery nadal sprawdzają środowisko podczas wykonania.
Wcześniej trzeba [zamrozić rzeczywiste polityki](ai09-prospective-policies.md)
dla bieżącego kodu oraz przygotować osobny interpreter zaakceptowanego Source.

```sh
python scripts/run_ai09_workflow.py \
  --root /private/campaign/development \
  --options /private/campaign/workflow-options.json \
  --policies /private/campaign/development-policies.json \
  --maximum-new-operations 1
```

Limit jednej nowej operacji pozwala zatrzymać pracę na granicy oryginalnego
kroku. Domyślnie wykonywany jest cały pozostały program. Katalog sesji ma
uprawnienia `0700`; `development-workflow.json` jest zapisany przed pierwszą
operacją. Osobne dzienniki Source i prognoz pozostają w katalogach każdej
skali. Ich oryginalne receipts, błędy i koszty nie są zastępowane nowym
zbiorczym rozliczeniem.

Przy wznowieniu wykonawcy ponownie sprawdzają dowody ukończonego prefiksu.
Nie uruchamiają ukończonych workerów. Zmiana opcji, kodu, polityk, metadanych
rodziców lub brak dowodów blokuje dalszą pracę. Zapisy intencji inicjalizacji
poza katalogami poszczególnych skal wykrywają także utratę całej sesji Source
albo dziennika prognoz. Nie odtwarzają zużytych prób po usunięciu katalogu.
Błąd lub niedokończona rezerwacja pozostają w oryginalnym dzienniku i nie
są automatycznie ponawiane.

Końcowy `development-workflow-result.json` wiąże zamrożony program
z oryginalnym `CompletedCanonicalForecast`, zawierającym wszystkie wcześniejsze
wyniki i koszty 25/50/100. Samo ukończenie programu nie jest kwalifikacją modelu.
Kalibracja, niezależna ocena trzech zastosowań, final na uzgodnionych seedach,
segmenty, niepewność, MLflow, model cards i lifecycle pozostają wymagane
przed oznaczeniem AI 09 jako `ready`.

Obecny dziennik prognoz kończy się na selekcji. Istniejący kontroler
kalibracji wymaga osobno zadeklarowanej operacji `calibrator_fit` oraz
oryginalnych powiązań eksportu, fitów i Tune. Nie można dopisać tej operacji
do już rozpoczętego dziennika ani przepisać wcześniejszych receipts na inny
protokół. Pełne powiązanie kalibracji i końcowej oceny trzeba przygotować
przed rzeczywistą kampanią. Zmiana modułów po jej rozpoczęciu zmienia
przypięcie runtime i blokuje dalsze wykonanie dotychczasowego protokołu.

[Odbiór 09.104](evidence/09-104-development-workflow.json) potwierdza 39 testów
źródeł i te same 39 z zainstalowanej paczki, bez pominięć. Obejmują cały program
i wznowienie z niezmienionymi kosztami.
To kontrole oryginalnych dzienników z jawnymi zastępnikami Source i modeli.
Pełny Mypy sprawdził 787 plików. Osobna kontrola rzeczywistych zależności
Source i środowiska modeli potwierdziła zgodność runtime; nie uruchamiała
workerów danych ani treningu. Wszystkie 654 moduły i 31 plików kontraktów
mają zgodne bajty w paczce i instalacji; 398 wcześniejszych kontraktów
pozostało bez zmian. Pełny CI, publikacja na main i rzeczywista kampania
pozostają wymagane.
