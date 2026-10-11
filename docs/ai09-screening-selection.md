# Wybór konfiguracji po pierwszym porównaniu

Nowa ścieżka zamraża selekcję przed inicjalizacją dziennika i pierwszym
eksportem. Zachowuje trzy eksporty, sześć fitów i dwa porównania z przygotowanego
programu. Dwunasta operacja odczytuje pełne pary etykiet i predykcji `tune`
przez istniejącego wykonawcę. Jej czas, pamięć, wynik i ewentualna porażka
pozostają rozliczone. Nie dodaje nowej próby treningu.

`rank_screening_families` zachowuje wszystkie sześć ocen i wybiera po jednej
konfiguracji RF, HGB oraz TensorFlow według MSE średniej, dotychczasowej bramki
biasu i pokrycia oraz kolejności wcześniej zamrożonych prób przy remisie.
Brak poprawnej konfiguracji dowolnej rodziny daje `not_ready`. Dla zerowego
popytu ilorazy pozostają nieokreślone. Wynik globalnego porównania z baseline'ami
pozostaje osobno: zwycięzca danej rodziny może przegrać z baseline'em.

Nowy protokół wiąże ranking z istniejącym receiptem selekcji i nie zmienia
starszego formatu tego receiptu. Weryfikator ponownie oblicza ranking z
utrwalonych metryk, sprawdza oryginalne receipts eksportów, fitów i scoringu
oraz wszystkie dwanaście kosztów. Zmiana oryginalnych rodziców blokuje odbiór.
Worker zapisuje również ranking w swoim lokalnym MLflow. Sam ranking nie
przyznaje dostępu final, awansu do większego profilu ani promocji.

Kontrola całego dziennika wykryła brak pomiaru pamięci eksportu. Eksport
mierzy teraz RSS bieżącego procesu i jego żywych procesów potomnych, co 50 ms
oraz na początku i końcu operacji. To próbkowana dolna granica szczytu;
nie obejmuje krótkich pików między próbkami ani zakończonych wcześniej
procesów. Nie jest nowym limitem zasobów: zewnętrzny nadzór nadal egzekwuje
budżety. Awaria pomiaru przed pracą blokuje odczyt Source, a późniejsza
blokuje zakończenie operacji z sukcesem. Koszt nieudanej pracy pozostaje
w dzienniku; wcześniejsze nieznane koszty pozostają nieznane.

**44 kontrole** pomiaru, eksportu i całego dwunastooperacyjnego dziennika
przeszły bez pominięć. Usunięcie oryginalnego receipt przygotowania, eksportu,
fitu lub scoringu blokuje ponowny odbiór rankingu. Testy używają jawnych
zastępników Source i modeli; nie dowodzą wykonania kampanii Project.
Pełny Mypy sprawdził **769 plików**, a generator sprawdził schematy v34.
Wcześniejsze 73 kontrole matematyki, par plików i istniejącej selekcji pozostają
zapisane osobno. Początkowe błędy typów, serializacji fixtures oraz rzeczywisty
brak RSS eksportu zostały poprawione, a ich nieudane wyniki zachowano.

[Dowód 09.97](evidence/09-97-screening-selection-and-export-memory.json) obejmuje
**214 regresji ze źródeł i te same 214 z zainstalowanej paczki**, bez pominięć.
640 modułów Python i sześć schematów v33/v34 mają identyczne bajty w źródłach,
paczce i instalacji. Wszystkie 372 wcześniejsze kontrakty pozostają niezmienione.
Testy obejmują także wcześniejsze eksporty, fity, scoring, warianty i dziennik.

Nowy protokół wieloskalowy zamraża budżety oraz osobne ścieżki dzienników
przygotowania i wykonania dla 50 i 100 produktów jeszcze przed pierwszą
próbą na 25 produktach. Nie można dopisać tych budżetów do rozpoczętego
dziennika ani odzyskać zużytej próby przez wybór nowej ścieżki. Zachowuje
limit 12 fitów: sześć na 25 produktach, trzy na 50 i trzy na 100.

`capture_completed_screening` ponownie sprawdza oryginalny ranking, receipts
i wszystkie dwanaście kosztów. `compile_forecast_advancement` wiąże wynik
z rzeczywistymi rodzicami 50 produktów i tworzy osiem operacji: trzy eksporty,
trzy fity zwycięzców rodzin, scoring oraz selekcję. Zmienia wyłącznie
tożsamości danych i budżety zadeklarowane przed próbami. Hiperparametry,
seed inicjalizacji, preprocessing, podziały czasu, pełna historia i polityki
pozostają takie same. Przygotowanie korzysta z tego samego przypiętego
planera Source; jego koszty nie znikają przy zmianie skali.

Dotychczasowi wykonawcy eksportu, fitu, scoringu i selekcji obsługują ten
protokół. Odbiór 50 produktów wymaga oryginalnych receipts i zmierzonych
kosztów obu skal, pełnego wspólnego Tune oraz poprawnych wyników wszystkich
trzech rodzin. Wczesna walidacja odrzuca niezgodne środowiska fitu i scoringu
przed pierwszym treningiem; wcześniej błąd był wykrywany dopiero przy scoringu.
Kontrole używają jawnych zastępników danych i workerów, w tym rzeczywistego
kontrolera RF z kontrolowanymi wynikami prepare/fit/reload.

[Dowód 09.98](evidence/09-98-frozen-forecast-advancement.json) zapisuje
**29 zaliczonych kontroli**, bez pominięć, oraz pełny Mypy dla **771 plików**.
Siedem nowych schematów v35 przeszło sprawdzenie; wszystkie 375 wcześniejszych
kontraktów pozostaje bez zmian. Zachowano także wcześniejsze błędy dwóch
kontrolnych fixtures. Szerszy zestaw 226 regresji źródłowych i tych samych
testów z paczki jest w toku; jego wynik nie został jeszcze odebrany.

Przed publikacją pozostają pełny lokalny CI oraz odbiór PR/main. Wykonawca
100 produktów nadal wymaga powiązania z oryginalnym pełnym rodzicem;
zamrożony budżet nie zastępuje jego implementacji ani uprawnienia do startu.
Nadal potrzebne są rzeczywiste dane i fity Project, powiązanie pozostałych
zastosowań, kalibracja, final i lifecycle. Canonical pozostaje wyłączony,
a jego limit RSS wynosi 12 GiB.
