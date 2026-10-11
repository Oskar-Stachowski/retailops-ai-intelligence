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
kontrolnych fixtures. **226 regresji źródłowych i te same 226 z paczki**
przeszły bez pominięć. 642 moduły i 13 schematów są identyczne w źródłach,
paczce i instalacji. Budowa początkowo nie wystartowała przez brak wolnej
pamięci w pięciominutowym oknie dopuszczenia. Następnie wykonano pozostałe
kroki na niezmienionym kodzie i przy tych samych zabezpieczeniach;
zaliczonych testów źródłowych nie powtarzano.

Dodatek [v37 / 09.100](evidence/09-100-canonical-forecast-execution.json)
wiąże wynik 50 produktów z [oryginalnymi rodzicami canonical 100](ai09-development-preparation.md).
`compile_canonical_forecast` ponownie sprawdza oryginalne wyniki 25/50,
wszystkie receipts i koszty oraz przygotowanie pełnych danych. Korzysta
z jedynej ścieżki i budżetów 100 zadeklarowanych przed pierwszą próbą 25.
Tworzy trzy eksporty, trzy fity wcześniejszych zwycięzców, wspólny scoring
Tune i selekcję. Nie zmienia hiperparametrów, preprocessingu ani seeda.
Niepoprawny wynik którejkolwiek rodziny na 50 blokuje kompilację.

Istniejące kontrolery rozpoznają osobny protokół pełnego development.
Oryginalne dowody obu poprzednich skal są sprawdzane przed eksportami
oraz podczas odbioru. Wynik zachowuje wszystkie koszty 25/50/100, także
koszty generacji, natywnego planowania i ponownego sprawdzania rodziców.
Samo ukończenie tej ścieżki nie zamyka AI 09 ani nie otwiera final.

Mypy sprawdził **775 plików**. Trzy schematy v37 zachowują wcześniejsze
388 plików JSON i plik ograniczeń Source. **133 kontrole wykonawców,
wariantów, portfolio i dziennika przeszły bez błędów i pominięć.**
Pierwszą kontrolę zatrzymał limit 64 MiB roboczego miejsca podczas tworzenia
cache Mypy, przed testami. Zachowano ten wynik; kolejna kontrola ma 256 MiB
miejsca na lokalny cache, ten sam limit 384 MiB RAM i rezerwy 1 GiB RAM
oraz 6 GiB dysku. Budżety właściwej kampanii pozostają niezmienione.
Kontrole używają jawnych zastępników workerów, bez danych i fitów Project.

Przed publikacją pozostają pełne regresje, lokalny CI oraz odbiór PR/main.
Nadal potrzebne są aktualizacja aktywnego pina Source, rzeczywisty workflow,
dane i fity Project, powiązanie pozostałych zastosowań, kalibracja, final
i lifecycle. Canonical pozostaje wyłączony, a jego limit RSS wynosi 12 GiB.

Pełny lokalny CI wcześniejszego przyrostu `8efd625` zakończył się błędem:
główny pytest miał **5580 zaliczeń i 55 pominięć**, lecz późniejszy zestaw
środowiska TensorFlow dał **3 zaliczenia i 5 odmów `preflight_reserve`**.
Pięć workerów nie wystartowało przez brak wymaganej wolnej pamięci.
Ten wynik pozostaje zapisany w 09.100; nie zalicza pełnego CI ani nowszego
kodu selekcji i przejść 50/100. Rezerwy pozostają bez zmian, a inne sesje
nie były zamykane na potrzeby kontroli.

Kontrola po rozszerzeniu do 100 produktów przekroczyła lokalny limit 900 s
przed ukończeniem pełnego zestawu. Zachowano jej częściowy wynik i koszty.
Profilowanie kopii metadanych wykazało wielokrotne serializacje i liczenie
tych samych skrótów. Kod używa teraz raz przygotowanych, w pełni sprawdzonych
bajtów przy liczeniu skrótu protokołu oraz bieżących wyników skrótów wewnątrz
pojedynczej walidacji. Nadal odczytuje wszystkie oryginalne dowody; nie dodaje
trwałego cache wyników sprawdzeń. Na tej samej kopii 2 MB pojedynczy odczyt
z profilerem skrócił się z 1,644 s do 1,135 s. To pomiar fragmentu, obejmujący
narzut profilera i importy, a nie szacunek przyspieszenia całej kampanii.
Kontrola po optymalizacji przeszła wszystkie 133 testy w 723,70 s; jej
nadzorca zakończył się sukcesem po 729,38 s. Nadal utworzyła dwa zestawy
przygotowania 25/50, mimo deklaracji session scope w importowanej fixture.
Wspólna rejestracja w conftest usuwa tę duplikację; zastępniki workerów są
zamykane przed przekazaniem wyniku. Pełny zestaw 20 plików testów źródeł
i identycznej paczki jest w toku i sprawdzi także dokładnie jedno wspólne
przygotowanie na proces. Lokalny limit każdej fazy paczki wynosi 1800 s,
limit RAM 384 MiB, a rezerwy 1 GiB RAM i 6 GiB dysku. Budżety kampanii
pozostają niezmienione.

Przy odbiorze paczki ponowne uruchomienie Mypy z pustym cache przekroczyło
lokalny limit 384 MiB (próbkowane 390,125 MiB), przed testami. Zachowano
nieudany pomiar. Wykorzystano wcześniej zaliczoną kontrolę wszystkich
775 plików i schematów na identycznym kodzie produkcyjnym; późniejsza zmiana
dotyczyła wyłącznie przygotowania pytest, którego Mypy nie obejmuje.
Pozostałe kroki paczki trwają przy tych samych zabezpieczeniach pamięci.
