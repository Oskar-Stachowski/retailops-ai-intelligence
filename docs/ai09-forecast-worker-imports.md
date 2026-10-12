# Pamięć startu workerów prognoz

Natywny job PR 68 dla kodu `60b4a03` wykonał siedem kontroli pomyślnie,
ale wspólny scoring sześciu modeli przekroczył deklarowany limit RSS 1 GiB.
Worker został zatrzymany przez własnego nadzorcę. To rzeczywista odmowa
zasobowa na Linux CPU, odrębna od lokalnych odmów startu z powodu rezerwy RAM.
Oryginalny wynik pozostaje niezaliczony.

Worker scoringu importował kontroler treningu, aby sprawdzić gotowy artefakt.
Import kontrolera wciągał również eksport, dziennik i rozbudowane deklaracje
kwalifikacji. `campaign_fit_artifact` udostępnia te same funkcje inwentaryzacji
i kontroli plików bez ładowania tej ścieżki sterującej. Dotychczasowy moduł
kontrolera nadal eksportuje obie funkcje pod wcześniejszymi nazwami.

Podobnie worker przygotowania kontekstu korzysta z `campaign_source_identity`
do sprawdzenia producenta i eksportera. Zachowano obie oryginalne funkcje,
kontrolę czystego kodu, commitów i obu locków. Dotychczasowy eksporter nadal
udostępnia wcześniejsze nazwy. Porównanie AST potwierdza niezmienioną treść
wszystkich czterech przeniesionych funkcji.

Trzy oddzielne uruchomienia importu scoringu przed wydzieleniem i trzy po nim
dały medianę szczytu RSS procesu **131 514 368 B → 78 397 440 B**: mniej
o **53 116 928 B**, około **40,4%**. Liczba załadowanych modułów zmalała
z 547 do 468. To lokalny pomiar zimnego importu na macOS, bez TensorFlow,
modeli i predykcji; nie dowodzi jeszcze kosztu całego scoringu na Linux.

Nowe kontrole uruchamiają oba workery w osobnych procesach i odrzucają
powrót importów kontrolera treningu, eksportera, kwalifikacji oraz MLflow
i TensorFlow podczas samego importu. Dotychczasowe testy nadal sprawdzają
pełne artefakty, podpisy, klucze, scoring, kontekst, eksport i final-export.
Natywny test wypisuje pełny pomiar nadzorcy przy błędzie, aby zachować również
konkretny zmierzony RSS i przyczynę zatrzymania.

[09.119](evidence/09-119-forecast-worker-imports.json) zapisuje **163 testy
kodu i te same 163 testy z zainstalowanej paczki**, Mypy dla 802 plików oraz
zgodność bajtów 669 modułów i 47 schematów w paczce. Wszystkie 129 modułów
zamkniętego stockout AI 08 i 416 dotychczasowych plików kontraktów pozostają
identyczne względem bazy poprawki. Zachowano również wcześniejsze nieudane
kontrole i ich konkretne przyczyny.

Limit natywnej kontroli **1 GiB**, rezerwy i wszystkie asercje pozostają
zachowane. Limit przyszłej diagnostyki canonical nadal wynosi **12 GiB**,
z rezerwą systemową **1 GiB**. Ponowny pełny CI dokładnego PR, w tym rzeczywisty
scoring i odtworzenie modeli, oraz CI wynikowego main pozostają wymagane.
Ta zmiana nie uruchamia kampanii Project ani nowych danych final.
