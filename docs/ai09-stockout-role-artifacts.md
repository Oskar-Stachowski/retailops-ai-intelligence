# Prywatne pliki dojrzałych ról stockout

`prepare_stockout_roles` zapisuje wynik [pełnego podziału stockout](ai09-stockout-mature-roles.md)
do prywatnego artefaktu. Kontrakt v49 przypina cały plan podziału, tożsamość
publicznych cech i plan odtworzenia oryginalnych etykiet. Pełny strumień musi
zostać odebrany wraz z końcowymi kontrolami rodziców przed publikacją plików.

`membership.jsonl` zachowuje każdy fizyczny klucz, przynależność, powód
niekwalifikowania i hashe oryginalnych cech oraz etykiety. Obejmuje również
obserwacje z nieznanym wynikiem i z przerw między oknami. Pięć osobnych plików
zawiera pełne cechy i etykiety wyłącznie kwalifikujących się obserwacji danej
roli. Puste role również mają własne pliki i jawne liczniki obu klas.
Żaden wiersz nie jest próbkowany ani zastępowany ujemną etykietą.

Manifest wiąże wszystkie pliki, ich rozmiary i hashe z oryginalnym świadectwem
pełnego podziału. Hashe kluczy i wyników każdej roli zachowują format wejść
treningowych AI 08. Weryfikacja sprawdza kolejność, unikalność, pełne członkostwo,
daty, powody niekwalifikowania i liczebności klas. Kontrola końcowa wykrywa
także zmianę wcześniej przeczytanego pliku podczas czytania następnych.
Publikacja korzysta z prywatnego katalogu, uprawnień 0700/0600, synchronizacji
dysku i atomowego przeniesienia bez nadpisywania istniejącego celu.

`verify_stockout_roles` odczytuje cały artefakt i wymaga rezerwacji dostępu do
wszystkich pięciu populacji. `load_stockout_role` otwiera tylko wybrany plik
roli i manifest. Zwraca wszystkie wejścia liczbowe w natywnym kształcie
`values`, osobno etykiety i świadectwo dopiero po sprawdzeniu całego pliku.
Sprawdza też, czy wybrany plik lub manifest nie zmieniły się po odczycie.
Jego świadectwo jawnie wskazuje `whole_artifact_verified=False`;
pozostałe pliki z etykietami nie są wtedy odczytywane.

Każda operacja wymaga jawnego `allow_evaluation_truth=True` przed odczytem.
Ten argument nie zastępuje rezerwacji w dzienniku kampanii. Manifest zawiera
zbiorcze liczniki podziału, więc dostęp do jego metadanych również wymaga
uwzględnienia we wspólnym protokole. Komponent nie nadaje uprawnienia do
treningu, oceny final ani promocji modelu i nie potwierdza wystarczającej próby.

Domyślny limit całego artefaktu to 2 GiB, a pełnej populacji milion wierszy.
Budowa i weryfikacja przebiegają strumieniowo; wybrana rola jest ładowana
w całości do wejść modelu. Nadzorca kampanii nadal egzekwuje łączny RAM,
dysk i czas. Pliki oraz scratch są częścią jej wspólnego budżetu.

Przed wykonaniem operacji Project pozostaje zamrożenie wspólnego protokołu
trzech zastosowań, zarezerwowanych treningów i kalibratorów, wyboru oryginalnego
artefaktu bez dodatkowego uczenia oraz progów i niezależnej oceny. Potrzebne
są także pełne kontrole reprezentatywnych profili 25/50, właściwa kampania,
MLflow, karty modeli i lifecycle oraz odbiór PR/main. Canonical pozostaje
wyłączony; limit wynosi **12 GiB**, rezerwa systemowa **1 GiB**.
AI 09 pozostaje **`not_ready`**.

[Odbiór 09.117](evidence/09-117-stockout-role-artifacts.json) potwierdza
94 testy kodu i te same 94 z zainstalowanej paczki, bez pominięć.
674 moduły oraz 53 schematy mają zgodne bajty. Kontrole obejmują
pełne oryginalne małe zestawy Source 1.1/1.2, wszystkie pliki ról, późne
zmiany danych i osobny syntetyczny plik 10 001 rekordów. Pierwszy błąd
typowania zachowano w dowodzie wraz z poprawką i pełnym ponowieniem kontroli.
