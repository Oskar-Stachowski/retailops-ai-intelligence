# Pełny publiczny parent anomaly w AI 09

`campaign_anomaly_parent.CampaignAnomalyPublicParent` weryfikuje rzeczywisty
publiczny snapshot i cały curated, a następnie udostępnia ograniczony parent
dla natywnego replay zdarzeń. Nie przyjmuje samego hasha jako dowodu zgodności
parenta. Nadrzędny kontroler musi wcześniej zarezerwować odczyt całego Source.

Adapter używa wspólnego `_open_verified_source_parent`: zamraża inwentarz
i manifesty, tworzy prywatne kopie, weryfikuje wszystkie bajty i typowane
tabele snapshotu, ponownie wykonuje pełną transformację curated i porównuje
cały dokument logiczny. Weryfikacja obejmuje również tabele, których projekcja
zdarzeń nie używa. Ponowna kontrola oryginałów, prywatnych kopii i runtime
odbywa się przed udostępnieniem parenta oraz po zakończeniu kontekstu.

Snapshot 1.1 ma bramki `forecast_source` i `inventory_source`. Snapshot 1.2
deklaruje także `anomaly_source`. Adapter respektuje te wersje. Dla 1.1 dowodem
jest pełna weryfikacja publicznych faktów inventory i nowa natywna projekcja
operacyjna. Wynik nie pożycza kwalifikacji Source z zamkniętego AI 07 ani nie
zmienia jego kontraktów i limitów.

Wszystkie wiersze sześciu tabel wymaganych przez `full_raw_dq.source.projection`
są odczytywane, niezależnie sprawdzane przez natywny `Digest` i indeksowane
na dysku. Każda sprzedaż i każda natywna reklamacja przechodzą przez tę samą
funkcję projekcji, z właściwymi publicznymi dependencies. Wybrany fragment
zawiera jedno zdarzenie i jego dependencies; cała populacja nie jest obcinana.
Liczba parent events musi dokładnie odpowiadać wszystkim sales i return events
w zweryfikowanym manifeście. Globalne UUID i klucze biznesowe pozostają wspólne
dla całego Source. `ParentFacts.match` nie został zmieniony.

Read-only `Mapping` i strumieniowane `Sequence` zastępują pełne kopie
słowników oraz listy parenta. Kolejność zdarzeń i faktów odpowiada natywnej
projekcji. Lookup używa pełnych indeksów, także dla globalnej proweniencji UUID.
Zapis canonical może usuwać końcowe zera z kwot; adapter odtwarza dokładną skalę
typu Arrow przed natywną projekcją, bez zaokrąglania. Dzięki temu wire, UUID
i business-version identity zachowują oryginalny format kwot.

Budżety obejmują całą bazę, jej przydzielone bloki i brudne strony, rollback
journal oraz pomocniczy `Digest`. Natywny digest korzysta z indeksu sortowania,
więc nie potrzebuje dodatkowego nieśledzonego sortowania na dysku. Odrębne
limity dotyczą rekordu i pojedynczego fragmentu projekcji. Przekroczenie
budżetu przerywa operację; nie zwraca pomniejszonego parenta.

Wartości indeksu sprawdzają własne hashe podczas lookup. Kontrola końcowa
ponownie sprawdza całą populację, globalne klucze i hashe zdarzeń oraz faktów.
Zmiana wiersza, także wraz z jego hashem, uniemożliwia wydanie receipt.
Awaria prywatnego stanu ma typ `RuntimeError`, aby natywny kernel nie potraktował
jej jako kwarantanny niepoprawnego zdarzenia. Parent działa wewnątrz jednego
kontekstu; receipt jest dostępny dopiero po jego poprawnym zakończeniu.

[Dowód 09.58](evidence/09-58-native-anomaly-public-parent.json) zapisuje 42
kontrole na rzeczywistych, wcześniej eksponowanych fixture ordinary 1.1 oraz
demand i physical 1.2. Wszystkie przeszły także z zainstalowanej paczki.
Pierwsze 12 błędów zachowano: dotyczyły różnic bramek wersji 1.1/1.2
i utraty skali kwot przed projekcją. Pełne CI i publikacja pozostają wymagane.

To dowód fizycznej zgodności Source i natywnej projekcji. Nie dowodzi
genealogii operacji generation, rezerwacji odczytu w dzienniku, business-day
completeness, kwalifikacji cech, prawdy offline ani jakości modeli. Do pełnej
kampanii pozostają day qualification, Point census, truth ordinary/demand/physical,
powiązanie z rzeczywistym dziennikiem, artefakty, koszty, wymagane grupy
i sparowana niepewność. Stockout i pozostałe wymagania AI 09 również pozostają
otwarte. `quality_qualified` i `stage_ready` są false.
