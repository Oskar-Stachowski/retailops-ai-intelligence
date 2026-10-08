# Prospektywna kampania AI 09

AI 07 i AI 08 są zamknięte. AI 09 przygotowuje wspólną końcową ocenę i pozostaje
`in_progress / not_ready`. [Odbiór 09.12](evidence/09-12-prospective-campaign-journal.md)
obejmuje rozliczenie zachowanej historii oraz trwały dziennik kolejnej kampanii.
Nie stanowi odbioru rzeczywistych danych, treningu portfolio ani final testu.

[Pełne portfolio v25](ai09-full-scenario-portfolio.md) rozszerza zamrożony zakres
o trzy pełne warianty ordinary/demand/physical dla development i każdego final
seeda, w jednym dzienniku i jednym wspólnym freeze. Dotychczasowy v10 i jego
schemy zachowano. Poniższy opis czterech źródeł dotyczy wersji bazowej.

## Historia poprzednich eksperymentów

Prywatne katalogi poprzedniej kampanii w `/private/tmp` nie istnieją. Moduł
`evaluation_campaign.legacy_carryover` czyta wyłącznie trzy przypięte publiczne
receipty z commita `7f10f8e`, sprawdza ich dokładne bajty i odtwarza opublikowane
metadane: 11 prób, 44 rozpoczęte i 40 zakończonych fitów, pięć zakończonych
prób, sześć przerwanych oraz dziesięć identyfikatorów protokołów.

Nie odzyskano oryginalnych dzienników, artefaktów ani historii rezerwacji przed
odczytem i treningiem. Cztery stare sloty fitów i 64 sloty odczytów są deklarowane
jako niedostępne; dostępny budżet dawnej kampanii wynosi zero. Nie jest to zapis
operacji w utraconym dzienniku. Późniejsze użycie i pełny historyczny koszt mają
stan `unknown_not_zero`. Znane źródło pozostaje wcześniej eksponowane, każde
nieopisane źródło ma świeżość `unknown_not_unseen`.

Nowy protokół wiąże pełny carryover i jego digest. Jawnie deklaruje odrębny,
prospektywny zakres oraz nowy budżet; nie przywraca starych slotów i nie nadaje
starym danym statusu nietkniętego holdoutu.

## Zamrożony zakres i kolejność

Kontrakty [v9](../contracts/evaluation/v9/legacy_campaign_carryover.schema.json)
i [v10](../contracts/evaluation/v10/prospective_campaign_protocol.schema.json)
zachowują wcześniejsze wersje kontraktów AI 09. Nowy protokół przypina:

- jeden development na seedzie 42, profil `ai-dev`: 365 dni, 100 produktów,
  pięć aktywnych par sprzedaży i trzy lokalizacje zapasu;
- trzy końcowe źródła 42/137/2026, profil `ai-training`: 730 dni, 200 produktów,
  dziesięć par sprzedaży i cztery lokalizacje zapasu;
- wspólną wersję producenta i locka, hashe rzeczywistych konfiguracji generacji,
  późniejsze końcowe originy i dojrzałość etykiet, runtime całego konsumenta;
- skończoną listę operacji, ich receptury, role, prerequisites i maksymalną
  liczbę prób, w tym osobny licznik fitów oraz seed inicjalizacji modelu;
- równe budżety prób i seedów inicjalizacji dla RF, HGB i TensorFlow; freeze
  wymaga zakończonego treningu każdej z tych trzech rodzin;
- politykę wyboru, trzy polityki jakości, segmenty, niepewność oraz dodatnie
  wagi każdego z trzech seedów i czterech scenariuszy, ustalone przed wynikami.

Każde źródło ma jedną zaplanowaną generację. Odczyt pełnych rodziców jawnie
używa roli `all_parent_data`, zamiast deklarować ekspozycję tylko jednej roli.
Operacje zależą od zakończonej generacji właściwego źródła. Każdy końcowy seed
musi mieć ocenę forecast, anomaly i stockout. Mniejszy profil nazwany
`ai-training`, brak seeda lub zastosowania oraz dodatkowy niezadeklarowany
budżet są odrzucane przed wykonaniem.

Warunek dat w samym `CampaignProtocol` sprawdza minimalną separację od
zadanego development. Nie zastępuje inventory wszystkich wcześniejszych
odczytów z AI 06–08 i AI 09 ani nie kwalifikuje globalnej świeżości. Źródła
[końcowego AI 08](reference/stockout-resource-pilot-1.8-future-seed42.json)
obejmują dane do 2026-09-18. Rzeczywiste końcowe okna AI 09 trzeba ustalić po
sprawdzeniu wszystkich znanych zakresów rodziców, targetów i dostępności;
nie wolno przyjąć wcześniejszego okna tylko dlatego, że przechodzi minimalny
walidator. Inny dataset ID, profile, seed albo parametr nie dowodzi braku
wcześniejszej ekspozycji. Final gen/read pozostają po selection freeze, a
osobny dowód generacji i historii dostępu jest wymagany przed kwalifikacją.

Fit modelu jest dozwolony wyłącznie na development/train, fit kalibratora na
development/calibration. Przed generacją lub odczytem końcowego źródła trzeba
utrwalić `SelectionFreeze`: trzy pakiety model/preprocessing/calibration/
threshold/feature-schema oraz dowody wyboru, związane z dokładnym headem całej
historii development, łącznie z nieudanymi próbami. Po freeze nie można wznowić
development, podmienić wyboru ani ponownie wygenerować końcowego źródła.

## Trwałość i koszty

`evaluation_campaign.campaign_journal.initialize` zamraża protokół w jednym
jawnym absolutnym `journal_path`. Katalog ma uprawnienia 0700, dziennik i lock
0600. Odczyty nie podążają za symlinkami. Operacje są serializowane przez flock;
publikacja używa prywatnego pliku tymczasowego, fsync, atomic replace oraz fsync
katalogu. Każda rezerwacja jest opublikowana przed wejściem do ciała
`audited_operation`, które obejmuje całą generację, odczyt lub trening.

Sukces wymaga digestu zweryfikowanego wyniku i pomiaru czasu. Zakończony fit
wymaga również peak RSS całego własnego drzewa procesów i rozmiaru artefaktu.
Runner dostarcza te pomiary i rzeczywisty receipt; dziennik nie wymyśla kosztu
z liczby rekordów. Zwykły wyjątek zapisuje zmierzony wall time; dostępne częściowe
pomiary RSS/artefaktu są zachowane, a brakujące pozostają nieznane. Awaria nie
zwraca budżetu. SIGKILL pozostawia trwale
rozliczoną, nierozstrzygniętą rezerwację; jej koszt pozostaje nieznany.
Nierozstrzygnięta próba blokuje ponowny start tej samej operacji i freeze.
Kontrolowane rozliczenie jej jako failure nie zwraca zużytego slotu.
Summary podaje osobno liczbę nowych prób bez pomiaru czasu i fitów bez pełnych
pomiarów zasobów, aby brakujący koszt nie został policzony jako zero.

Ponowna inicjalizacja istniejącego protokołu zachowuje wszystkie zdarzenia.
Zmiana polityki lub utrata `journal.json` w istniejącym katalogu powoduje błąd,
bez zerowania liczników. Nie należy tworzyć drugiego katalogu dla tej samej
kampanii. Dziennik jest lokalnym audytem współpracujących runnerów; nie wykrywa
odczytów wykonanych poza nimi ani celowego przywrócenia całej wcześniejszej kopii
przez właściciela plików. Hash chain nie jest zewnętrznym, niezmiennym kotwiczeniem.

Pełny runtime musi odpowiadać zamrożonemu protokołowi przed nowym wykonaniem lub
zapisem sukcesu. Po zmianie runtime można nadal odczytać historię i zapisać
failure istniejącej rezerwacji. Kampanię należy wykonywać z zachowanego,
przypiętego środowiska/wheela; aktualizacja kodu nie daje nowego budżetu.

`close` wymaga rozstrzygnięcia wszystkich rezerwacji i wykonania wszystkich
zaplanowanych końcowych operacji. Zakończenie operacji oceny może zawierać
negatywny wynik quality gate; taki wynik pozostaje w raporcie i nie upoważnia do
promocji. Zamknięcie wykonania wiąże raport, lecz flagi jakości, świeżości holdoutu
i `stage_ready` nadal są false. Kwalifikację zapewniają osobne weryfikatory danych,
oceny i lifecycle.

## Audytowany eksport development

[Receipt 09.14](evidence/09-14-audited-development-export.json) oddziela
kontrolowane testy od niewykonanego jeszcze canonical przebiegu.

Nowe [kontrakty v12](../contracts/evaluation/v12/campaign_development_export_plan.schema.json)
zamrażają `CampaignDevelopmentExportPlan` przed generacją. Plan zawiera dokładne
role, originy, cutoffs, features i limity, bez nieznanych jeszcze parent IDs.
Jego digest musi być `execution_recipe_sha256` zaplanowanej operacji
`source_read / development / all_parent_data`.

`campaign_export.export_development_forecast` trwale rezerwuje tę operację przed
odczytem metadanych, hashami i replayem. `CampaignGeneratedParentReceipt` musi
być dokładnym dowodem zakończonej generacji tego samego protokołu, źródła,
rezerwacji i runtime. Samo stworzenie obiektu receiptu nie potwierdza generacji.
Digest pełnych resolved parameters musi odpowiadać zamrożonemu
`generation_config_sha256`; profil, seed, rozmiary i historia muszą odpowiadać
canonical `ai-dev`. Zmiana ról, polityki lub limitów jest odrzucana przed parent I/O.

Ten sam zweryfikowany prywatny snapshot dostarcza Git commit/clean state/lock
producenta oraz commit i lock eksportera. Commit i lock źródła muszą odpowiadać
protokołowi; lock eksportera musi odpowiadać deklarowanemu plikowi zależności
producenta. Dopiero potem builder może przeliczyć całą populację. Weryfikacja
wyniku i końcowe guardy rodziców/runtime kończą się przed publikacją receiptu.
Receipt jest fsync/no-replace w prywatnym `journal/receipts` (0700/0600), a jego
digest i zmierzony wall/rozmiar artefaktu trafiają do zakończonej operacji.

`validate_completed_export` sprawdza dziennik i dokładne bajty prywatnego
receiptu. Nie czyta danych ani nie autoryzuje kolejnego odczytu/fitów. Wynik
pozostaje dowodem kolejności i budżetu lokalnego współpracującego runnera;
nie nadaje kwalifikacji globalnej ekspozycji, świeżości, zasobów ani jakości.
Manifest fizycznego artefaktu nadal zachowuje wszystkie flagi false. Końcowy
export/holdout wymaga odrębnego jawnego API po selection freeze.

## Pozostały rzeczywisty odbiór

W tym przyroście nie inicjalizowano nowego dziennika projektu i nie wygenerowano
nowych danych projektu. Testy używają jawnych, kontrolowanych metadanych.
Przed uruchomieniem kampanii trzeba przygotować kompletne receptury i polityki,
odebrać pełne wykonanie audytowanych runnerów generacji/curation i treningu
oraz dokończyć kalibrację i niezależną ocenę.
Powiązanie development read z [fizycznym eksporterem](physical-forecast-export.md)
jest zaimplementowane; nie wykonano jeszcze pełnego canonical przebiegu tego API.
Eksporter odtwarza features i etykiety z tych samych prywatnych rodziców; jego
odrębny odbiór diagnostyczny nie zastępuje pomiaru pełnego profilu i budżetów.
Następnie wymagane są fair training/kalibracja, zamrożona ocena końcowa,
segmenty, niepewność, koszty, trzy raporty/karty, MLflow/lifecycle i odbiór main.
