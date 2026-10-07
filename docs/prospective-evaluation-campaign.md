# Prospektywna kampania AI 09

AI 07 i AI 08 są zamknięte. AI 09 przygotowuje wspólną końcową ocenę i pozostaje
`in_progress / not_ready`. [Odbiór 09.12](evidence/09-12-prospective-campaign-journal.md)
obejmuje rozliczenie zachowanej historii oraz trwały dziennik kolejnej kampanii.
Nie stanowi odbioru rzeczywistych danych, treningu portfolio ani final testu.

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
z liczby rekordów. Awaria nie zwraca budżetu. SIGKILL pozostawia trwale
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

## Pozostały rzeczywisty odbiór

W tym przyroście nie inicjalizowano nowego dziennika projektu i nie wygenerowano
nowych danych projektu. Testy używają jawnych, kontrolowanych metadanych.
Przed uruchomieniem kampanii trzeba przygotować kompletne receptury i polityki,
podłączyć audyt do rzeczywistego eksportera pięciu ról i runnerów, odtworzyć features
z tych samych kwalifikowanych rodziców oraz zmierzyć pełny profil i budżety.
Następnie wymagane są fair training/kalibracja, zamrożona ocena końcowa,
segmenty, niepewność, koszty, trzy raporty/karty, MLflow/lifecycle i odbiór main.
