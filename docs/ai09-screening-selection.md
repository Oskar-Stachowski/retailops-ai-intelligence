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

Przed publikacją pozostają pełny lokalny CI oraz odbiór PR/main. Kolejny wykonawca musi
przenieść dokładnie wybrane pierwotne konfiguracje i wszystkie koszty do
zamrożonych profili 50 i 100. Nadal potrzebne są rzeczywiste dane i fit,
powiązanie pozostałych zastosowań, kalibracja, final i lifecycle.
