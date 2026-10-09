# Pełne historyczne cechy anomaly na dysku

`CampaignAnomalyFeatureProjection` wyprowadza Point dla każdego zadeklarowanego
dnia sprzedaży i zwrotu, z [natywnej bramki raw-DQ](ai09-native-anomaly-day-gate.md)
tego samego całego publicznego Source. Plan przypina plan bramki, hash wszystkich
dni, oryginalną politykę i dokładną liczbę Point. Wynik pozostaje uporządkowany
według natywnego klucza serii i daty, do późniejszego scoringu w partiach.

Adapter wczytuje na dysk wszystkie cztery tabele kontekstu i niezależnie sprawdza
ich pełne liczby, digesty, zakresy i klucze. Zachowuje kolejność wierszy oraz
osobne hashe liczone podczas wczytywania. Dla pojedynczego dnia pobiera wszystkie
wersje planów danego produktu, wszystkie wersje mapowania dla jego daty i serii
oraz wszystkie wersje inventory dla produktu i daty. Nie filtruje planów przed
natywnym wyborem wersji. Waluta pozostaje wyłączona z klucza mapowania, zgodnie
z oryginalnym kodem, i nadal uczestniczy w grain dnia i doborze ceny.

Niezmienione `Features.point` i `Features.context` wyznaczają 28 dni historii,
fit do początku dnia minus mikrosekunda, ocenę na końcu dnia z opóźnieniem
sprzedaży lub zwrotów, kwalifikację raw-DQ, mediany, skalę, statusy i referencje.
Wartości quantity ani quality z daily versions nie zastępują obserwacji raw-DQ.
Nieznane wartości i niekwalifikowane dni zachowują natywne statusy i null.

[Przygotowanie 09.63](evidence/09-63-native-anomaly-feature-preparation.json)
zachowuje pierwsze 32 passed / 2 failed i przerwanie własnej nowej kontroli.
Błąd dotyczył zbyt małego własnego limitu Point: późne fakty w natywnym
kontrakcie mogą dostarczyć długie listy braków. Poprawiona wersja zachowuje
oryginalny limit bajtów natywnego artefaktu. Poprawiona wersja zaliczyła
98 kontroli natywnych i 64 kontroli zainstalowanego pakietu, bez pominięć.

Pełne natywne JSON Point są kompresowane zlib level 1, bez usuwania historii
lub identyfikatorów. Odczyt ogranicza rozmiar po dekompresji, odrzuca niepełny
strumień i dodatkowe bajty, sprawdza checksum i pełny klucz. Hashe zapisu
powstają w rzeczywistej chwili produkcji, przed kontrolą SQL. Kontrola pełnego
census porównuje oryginalne bajty i klucze z tymi stałymi hashami; nie musi
powtórnie alokować wszystkich zagnieżdżonych obiektów historii. Publiczny
odczyt nadal waliduje każdy Point. Koszt pełnego profilu nie został zmierzony.

Pamięć obejmuje jedną ograniczoną grupę kontekstu i natywny Point. Przekroczenie
budżetu grupy, Point lub łącznego indeksu kończy całą operację błędem. Budżet
indeksu uwzględnia zaalokowane pliki, dirty pages, pomocniczy digest i rollback
journal. Po zachowaniu wszystkich Point adapter zwalnia tylko indeks kontekstu,
a następnie ponownie sprawdza hash pełnego census. Nie podnosi limitu 10000
Point natywnego modelu i nie przyznaje kwalifikacji dla kampanii.

Wykonane kontrole porównują pełny wynik z oryginalnym pipeline na publicznych źródłach
ordinary 1.1 oraz demand/physical 1.2, również przy brakujących i spóźnionych
faktach, kwarantannie i różnych opóźnieniach polityki. Kontrole zmienionych
Point, ponownego zapieczętowania, lookup keys, brakujących rekordów, budżetów
i przerwania kontekstu blokują ukończony receipt. Pełny `make ci-local`, chroniony
PR i odbiór dokładnego head oraz wynikowego main pozostają wymagane.

Wykonany pełny lokalny przebieg zaliczył 5038 testów, z 49 udokumentowanymi
pominięciami. Pięć kontroli TensorFlow odmówiło startu przez wymaganą rezerwę
pamięci; trzy pozostałe przeszły. Pełny odbiór na izolowanym runnerze pozostaje
wymagany. Obsługa własnych tabel producenta `planned-source-cached-execution-1.1.3`
zachowuje wszystkie kontrole tożsamości i niezależny odczyt; 43 kontrole natywne
i 43 kontrole końcowej paczki przeszły.

[Weryfikacja skanera](evidence/09-64-historical-scanner-verification.json)
klasyfikuje 11 historycznych fałszywych alarmów. Wyjątki dotyczą tylko dokładnych
commitów, plików, reguł i linii. Cała historia i obecne pliki przechodzą skan;
syntetyczne nowe poświadczenie w tej samej ścieżce nadal jest wykrywane.

Receipt wymaga późniejszego poprawnego zakończenia zewnętrznych kontekstów
bramki, dni, replay i publicznego parenta. Genealogia generation, zamknięcie
producenta, dziennik odczytów, niezależna prawda offline, pełna ocena jakości,
koszty, grupy krytyczne i sparowana niepewność pozostają osobnymi warunkami.
`quality_qualified` i `stage_ready` pozostają false.
