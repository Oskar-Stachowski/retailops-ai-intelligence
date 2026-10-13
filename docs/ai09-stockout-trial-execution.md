# Zarezerwowane próby stockout

`run_campaign_stockout_trial` wykonuje jedną wcześniej zadeklarowaną próbę:
bazowy LR/HGB albo warunkowy kalibrator z zamrożonej siatki C. Korzysta
z przygotowanego pełnego pliku dojrzałej roli. Rezerwuje operację w dzienniku
przed odczytem danych, sprawdza ukończone przygotowanie, wspólny protokół,
runtime, oryginalny model bazowy i recepturę. Błąd pozostaje rozliczony;
ponowienie nie odzyskuje próby. Błąd bazy pozwala zapisać odrzucone zależne
próby, ale nie pozwala zakończyć ich jako udane.

Baza odczytuje tylko `base_train`, a kalibrator tylko późniejsze
`calibrator_fit`. Wspólne daty kampanii wyznaczają chwilę dostępności modelu.
Uczenie zachowuje pełną kwalifikującą się populację, preprocessing train-only,
oryginalne równania i konfiguracje AI 08. Kalibrator używa zapisanej bazy;
nie uruchamia jej ponownego treningu.

Oddzielny świeży proces wczytuje wynik i sprawdza przenośne równania na tych
samych wejściach dopuszczonych do danej próby. Nie zmienia dat obserwacji
ani nie odczytuje późniejszej roli, aby wykonać ten test. Jest to kontrola
numeryczna odtworzenia, nie pomiar jakości. Porównanie obejmuje wszystkie
wiersze, z tolerancją względną `1e-10` i bezwzględną `1e-12`.

Publikowany artefakt zawiera tylko typowany stan modelu i jego powiązania.
Wektor pomocniczy do kontroli odtworzenia pozostaje w prywatnym katalogu
próby poza artefaktem modelu. Kolejne kalibratory nie otrzymują przez ten
artefakt predykcji dla wcześniejszych wierszy treningowych. Odbiór wymaga
zgodności wszystkich plików, pełnych hashy kluczy i etykiet, kosztów,
budżetów i trwałego zapisu przed zakończeniem operacji w dzienniku.

Pełny dowód przygotowania występuje w końcowym receipt tylko raz. Zapisy
obu faz zachowują jego hash oraz wszystkie pomiary. Usuwa to powielanie
tej samej historii w metadanych; wcześniejsza kontrola wykryła przekroczenie
limitu zapisu przez trzy kopie. Limit receipt **512 KiB** pozostaje zachowany,
a odbiór wymaga identycznego dowodu po treningu i po zimnym odtworzeniu.

**191 kontroli kodu przeszło**, bez pominięć; Mypy sprawdził 819 plików.
Zgodne są bajty 686 modułów i 59 schematów w kodzie, wheel i instalacji.
Testy zainstalowanej paczki zatrzymał nadzorca po spadku wolnej pamięci
poniżej niezmienionej rezerwy 1 GiB. Odbiór paczki pozostaje otwarty.
[09.124](evidence/09-124-stockout-trial-execution.json) zachowuje wszystkie
wcześniejsze błędy i pomiary. Nowe testy używają jawnie syntetycznych
plików ról i zastępczych metadanych generacji/przygotowania oraz pomiaru
nadzorcy. Wykonują rzeczywiste dopasowanie modeli, oryginalne czytniki,
dziennik i osobne procesy odtworzenia. Nie dowodzą wykonania kampanii Source
ani kwalifikacji modeli. Właściwy pomiar całego drzewa procesów nadal należy
do odbioru zasobowego i reprezentatywnego profilu.

Wybór oryginalnych artefaktów, polityki progów, niezależna ocena, pełny
workflow trzech zastosowań 25/50/100, kampania i final, MLflow, lifecycle
oraz odbiór PR/main pozostają wymagane. Canonical wyłączony, limit **12 GiB**,
rezerwa **1 GiB**; AI 09 pozostaje **`not_ready`**.

Dziennik zachowuje dwa jawnie dopuszczone kody odrzucenia: niedodatnie
nachylenie kalibratora i brak zbieżności. Pozostałe wyjątki nadal otrzymują
ogólny kod; surowa treść wyjątku nie trafia do dziennika. Kontrole sprawdzają
zarówno zapis oryginalnej przyczyny, jak i zachowanie kosztu nieudanej próby.
