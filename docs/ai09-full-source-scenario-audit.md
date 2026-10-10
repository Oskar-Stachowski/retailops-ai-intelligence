# Audyt pełnych profili Source dla anomalii

[Dowód 09.88](evidence/09-88-full-source-scenario-audit.json) ujawnia przeszkodę
niewidoczną w samej kontroli kontraktów: Source przyjmował parametry i plany
25/50, ale jego niezależna weryfikacja efektów używała builderów ograniczonych
do 5000 dziennych ziaren. Krótka kontrola potwierdziła odrzucenie demand i
physical przy 45 625 oraz 91 250 ziarnach, przed budowaniem danych.

Dlatego wykonawca przygotowania v30 nie oznacza jeszcze, że istniejący pin
Source może ukończyć oba zaplanowane warianty. Limit pamięci 12 GiB nie zmienia
tego ograniczenia semantycznego. Pełny canonical i final także przekraczają
5000 ziaren; potrzebna jest poprawka natywnego producenta.

Poprawka `57a50e2` jest w [Source PR 113](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/113),
na bazie zaakceptowanego `8479b5d`. Dopuszcza jawne pełne profile w publikacji Source, zachowuje
limit samodzielnych builderów AI 07 oraz wszystkie natywne porównania efektów
i tabel. Zwalnia pełny przebieg kontrolny przed budowaniem drugiego oraz
strukturę kandydata przed odtwarzaniem całego Source. Nie zmienia losowań,
interwencji, populacji ani metryk. Sześć natywnych kontroli
30 × 8 × 3 × 2 potwierdziło identyczną całą zawartość wyników i efektów
demand/physical dla seedów42/137/2026. Regresja scenariuszy zaliczyła 85 testów,
a stałe fingerprinty oraz natywna publikacja/odczyt, qualification, eksport
i snapshoty — 29 testów, bez pominięć. Wszystkie kontrole używały małych
profili kontrolnych. Pełna skala i Required CI producenta pozostają otwarte.
Pełny lokalny preflight Source nie został wykonany ze względu na rezerwę RAM;
PR jawnie odnotowuje tę granicę, a standardowe CI działa na osobnych runnerach.

Consumer rozpoznaje przyszłą wersję `planned-source-cached-execution-1.1.4`
jako obsługującą dotychczasowy zapis z przekazaniem własności tabel.
12 kontroli backendu przechodzi; to kontrolowane moduły testowe, nie odbiór
nowego Source. Aktywny pin `ff2504a` pozostaje bez zmian do akceptacji poprawki.

Nadal wymagane są rzeczywiste plany większych scenariuszy, właściwe natywne
ziarna, pełne wykonanie i krytyczne pokrycie. Nie rozpoczęto kolejnego canonical,
projektowych fitów ani nowego dostępu do final. AI 09 pozostaje `not_ready`.
