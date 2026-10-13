# Wykonanie przygotowania stockout w kampanii

`prepare_campaign_stockout` łączy publiczne cechy i historyczny upstream,
odtworzenie oryginalnych etykiet oraz pięć dojrzałych ról z trwałym dziennikiem
kampanii. Nowy plan v51 zamraża wspólne daty, polityki i zasoby przed odczytem.
Powstałe identyfikatory danych są wiązane z ukończonym odbiorem generacji.

Wykonawca wymaga nowego protokołu z jawnymi rolami. Rezerwuje dokładnie jedną
operację `source_read` przed otwarciem rodziców, sprawdza recepturę, runtime,
oryginalną konfigurację Source i trwały zapis zakończonej generacji. Zmieniony
plan lub receipt nie uprawnia do odczytu. Wszystkie daty ról muszą odpowiadać
wspólnej deklaracji kampanii.

Trzy osobne procesy wykonują przygotowanie publiczne, dojrzałe role oraz
ponowną pełną weryfikację artefaktów. Każdy pracuje pod nadzorcą z tym samym
łącznym terminem i budżetem scratch. Błąd zachowuje zużyty czas i zmierzony
RSS; brak pomiaru pozostaje nieznany. Odbiór powstaje dopiero po sprawdzeniu
wszystkich plików i zostaje utrwalony przed zakończeniem operacji w dzienniku.
Ponowienie tej samej próby nie odzyskuje budżetu.

[09.122](evidence/09-122-stockout-preparation-execution.json) potwierdza **162
testy kodu i te same 162 testy z zainstalowanej paczki**, bez pominięć.
Mypy sprawdził **814 plików**. Wszystkie **681 modułów i 57 schematów**
v33–v51 mają zgodne bajty w źródłach, paczce i instalacji. Zachowano wszystkie
129 modułów zamkniętego AI 08 oraz 424 wcześniejsze pliki kontraktów.

Kontrole obejmują również odmowę startu z nieznanym RSS, błędny pomiar workera,
przekroczenie zasobów, zmianę pliku po weryfikacji i zachowanie wcześniejszych
eksporterów, generacji oraz dziennika. Wcześniejsze błędy fixtures i odmowa
admission pozostają w dowodzie. Osobne testy używają jawnych doubles dla
kosztów i workerów, a testy fizycznych faz korzystają z wcześniej istniejących
fixtures. Żaden z tych wyników nie oznacza wykonania nowej kampanii Project.
Połączenie z całym workflow 25/50/100, zarezerwowane treningi i selekcja,
ocena trzech zastosowań, final, MLflow i lifecycle pozostają wymagane.
Canonical wyłączony, **12 GiB** z rezerwą **1 GiB**; AI 09 **`not_ready`**.

Trzy istniejące fizyczne fixtures sprawdzają pełny przebieg przygotowania,
lecz dają **0 kwalifikujących się wierszy w każdej z pięciu ról**
(przy 60/480/480 wierszach członkostwa). Nie są dodatnim dowodem treningu
na rzeczywistej, przygotowanej populacji. [09.125](evidence/09-125-stockout-integration-progress.json)
zapisuje liczniki z zachowanych manifestów. Taki dowód pozostaje wymagany
w reprezentatywnym przebiegu całego workflow.
