# Niezależna prawda dla zwykłego Source

`read_campaign_ordinary_truth` tworzy prawdę offline dla zwykłego Source 2.7.
Najpierw rezerwuje odczyt w trwałym dzienniku, sprawdza zamrożony plan,
zakończoną generację i jej zapisany receipt. Worker działa pod przypiętym
interpreterem producenta. Odczytuje wszystkie tabele, prywatną konfigurację
i raporty przez `data.inventory.source_dataset_io.read_source_dataset`.
Natywny reader niezależnie odtwarza raporty z faktów. Sam brak pola scenariusza
albo poprawne sumy kontrolne nie dowodzą zwykłego, kompletnego zbioru.

Source zwraca typowane wiersze inventory oraz tekst CSV dla commerce.
Worker korzysta z konwersji skalarnych samego producenta. Tekst `false`
nie staje się kompletnym dniem, a długości polityki zwrotów są liczbami.
Kompletne okna sprzedaży obejmują wszystkie poprawne, kompletne dni Source;
luki pozostają nieznane. Okna zwrotów wymagają rzeczywistych zakupów rodzica
i kończą się zgodnie z polityką zwrotów. Zegar prawdy uwzględnia dostępność,
politykę opóźnienia ingestii i dotychczasową dojrzałość 24/72 h.

Oddzielne kontrakty `OrdinaryTruth`, `CampaignOrdinaryAnomalyCensusPlan`
i `CampaignOrdinaryAnomalyCensusEvaluation` nie zmieniają starych kontraktów
prawdy i ewaluacji scenariuszy. Zwykła prawda nie ma epizodów ani fikcyjnego
hasha scenariusza. Wiąże pełną weryfikację Source i receipt generacji.
Obie rodziny detektorów zachowują natywne metryki fałszywych alarmów oraz
pokrycia. Brak pozytywów nie daje idealnego recall ani average precision.
Prawda służy ocenie offline; nie trafia do cech ani treningu.

Bundle zawiera `truth.json`, `verification.json` i `plan.json`.
Publikację jako `completed` poprzedzają sprawdzenie całej zawartości,
fsync i prywatny trwały receipt. Każda nieudana zarezerwowana próba zachowuje
koszt i zużywa budżet. Odczyt final wymaga również rzeczywistej weryfikacji
zakończonego wyboru dla forecast, anomaly i stockout; sama deklaracja
zamrożenia wyboru nie wystarcza. Receipt wiąże hash tego wyboru.

[Dowód 09.85](evidence/09-85-ordinary-anomaly-truth.json) obejmuje 65 testów
ze źródeł i te same 65 z zainstalowanej paczki, bez pominięć.
Mypy obejmuje 752 pliki, a 623 moduły Python i sześć nowych schematów
zachowują zgodność paczki ze źródłami. Natywny odczyt wcześniej ujawnionego
małego zbioru sprawdził 58 tabel, 4044 rekordy i 292 kompletne obserwacje.
Druga kontrola zmieniła raport w prywatnej kopii i ponownie obliczyła jego
checksum w manifeście: niezależny reader poprawnie odrzucił fałszywy raport.
Pierwsza próba została zatrzymana przed odczytem przez rezerwę RAM i pozostaje
w zapisach. Udana próba z mniej kosztownym kontrolerem zachowała te same limity.

To dowód działania komponentu na małym zbiorze. Nie wykonano nowej generacji
Project, treningu Project ani świeżego odczytu final. Pełny lokalny CI gałęzi,
chroniona akceptacja i publikacja na main pozostają wymagane. Nadal trzeba
połączyć kompletne zwykłe i planowane oceny z rzeczywistą kampanią, pokryciem
grup, niepewnością i końcową kwalifikacją trzech zastosowań. AI 09 pozostaje
`not_ready`; limit canonical wynosi 12 GiB.

Audyt natywnych scenariuszy wykrył dodatkową granicę starszego adaptera
`source_truth`: zmiana popytu jednego SKU przestawia wspólne koszyki, co może
zmienić późniejsze zwroty i zapas innego SKU. W rzeczywistym małym Source
okno 2026-05-25–2026-05-31 miało ten sam popyt 172 sztuk, ale sprzedaż
119 w zwykłym przebiegu i 116 po wcześniejszych interwencjach. Starsza reguła
SKU oznaczała je jako clean, mimo braku bezpośredniej interwencji tego produktu
do końca okna. [Dowód 09.92](evidence/09-92-native-planning-and-truth-boundary.json)
wiąże diagnozę z natywnym Source 2.8, odtworzonymi efektami i dokładnym kodem.

Dlatego stary adapter odrzuca teraz profile `ai-dev` i `ai-training` przed
odczytem prywatnej prawdy. To blokada nieuzasadnionych etykiet, a nie nowy
adapter kwalifikujący planowane dane Project. Potrzebny jest osobny, wersjonowany
odbiór prawdy uwzględniający pośrednie efekty; zwykły Source pozostaje odrębną
podstawą negatywów. Historycznych artefaktów AI 07 nie przeliczano. Krytyczne
pokrycie, nowe próby Project oraz canonical pozostają niedopuszczone do czasu
zamknięcia tej granicy i pozostałych bramek kampanii.

Pierwszym elementem tego odbioru jest teraz `PairedSourceComparison`
([09.93](evidence/09-93-paired-source-comparison.json)). Porównuje wszystkie
wiersze i kolumny dokładnie 58 tabel zwykłego i planowanego Source. Zachowuje
liczność rekordów, więc usunięcia i duplikaty nie znikają przez deduplikację.
Zmiana nagłówka wspólnego koszyka dotyczy każdego powiązanego produktu;
plany dostaw i prywatne próbki dostawcy wiąże z rzeczywistym zamówieniem.
Zmiana wymiarów, polityk albo parametrów symulacji odrzuca całą parę.

Pierwsza różnica wyznacza konserwatywną granicę dla wszystkich kanałów
i obu typów zdarzeń produktu. Uwzględnia wcześniejszy początek okresu,
a nie tylko dzień późniejszego snapshotu. Od tej granicy albo od początku
bezpośredniej interwencji nie powstają nowe clean labels. Późniejsze równe
wiersze nie dowodzą powrotu do stanu bez wpływu interwencji. Kandydaci na
czyste okna są przecięciem kompletnych okien obu rodziców; luki i późniejszy
zegar dostępności pozostają zachowane. Wszystkie obserwacje nadal należą
do pełnego spisu ocenianych danych i raportu pokrycia.

Prywatny indeks SQLite pozwala zwolnić zwykły zbiór przed otwarciem
planowanego. Ma ograniczony cache i nie nadpisuje istniejącego pliku.
Nieudane albo niekompletne ładowanie nie może utworzyć dowodu ani wznowić
porównania z częściową bazą. **36 testów** przeszło ze źródeł i te same
36 z zainstalowanej paczki; 632 moduły Python mają identyczne bajty.

To komponent obliczeniowy, który sam nie otwiera Source i nie uprawnia
do odczytu Project. Nadal potrzebne są: niezależny pełny replay obu rodziców
pod przypiętym producer/runtime, wersjonowane pozytywne epizody i ich
dojrzałość, trwała rezerwacja obu odczytów oraz powiązany receipt kampanii.
Reguła może obniżyć udokumentowane pokrycie; bramki pokrycia nie zostały
złagodzone. Stan małych kontroli zasobów zapisuje 09.93; pełny lokalny CI,
Required CI oraz publikacja tego przyrostu na main pozostają otwarte.
