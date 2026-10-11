# Wykonanie przygotowania profili 25/50

Kontrakt [v30](../contracts/evaluation/v30/development_preparation_protocol.schema.json)
umożliwia przygotowanie trzech pełnych wariantów jednego profilu development:
ordinary, demand i physical. Korzysta z tych samych sześciu faz
`generate_campaign_parent`: generation, qualification, export, import, curation
i verify. Nie dodaje własnego generatora ani uproszczonej weryfikacji Source.
Profile zachowują [pełną historię i lokalizacje](ai09-development-profiles.md).

**Otwarta zależność Source:** [audyt 09.88](ai09-full-source-scenario-audit.md)
potwierdził limit 5000 ziaren w natywnej weryfikacji efektów obecnego pina.
Demand/physical w skali 25/50 wymagają zaakceptowanej poprawki producenta;
samo uruchomienie tego wrappera nie usuwa ograniczenia.

`compile_development_preparation` otrzymuje konkretny pełny protokół portfolio,
plan profilu oraz deklarację równego budżetu RF/HGB/TF. Sprawdza ich tożsamości,
wspólne wersje i locki producenta, seed, historię oraz lokalizacje. Zamrożony
plan zawiera trzy dokładne receptury generacji. Niezmienione kontrakty zwykłej
kampanii i pełnego portfolio odrzucają nowy, mniejszy profil.

`campaign_journal.initialize` zapisuje osobny prywatny dziennik, w którym cały
plan i deklaracja wyszukiwania istnieją przed pierwszym dostępem do Source.
`run_development_preparation_variant` wybiera recepturę wyłącznie z tego
dziennika. Trwale rezerwuje próbę przed sprawdzeniem producenta, uruchomieniem
workera i utworzeniem katalogów wynikowych. Nie inicjalizuje drugiej sesji ani
nie ponawia generacji po błędzie.

Każdy z trzech wariantów ma jedną próbę. Powodzenie wymaga wszystkich faz,
oryginalnej weryfikacji rodzica, zapisanej receipt oraz pomiaru czasu, RAM i
rozmiaru artefaktów. Zwykła awaria zachowuje zaobserwowane koszty i zużytą próbę.
Przerwanie bez zakończenia pozostawia nierozliczoną rezerwację z nieznanym
kosztem. Równoczesne żądania tej samej operacji nie tworzą dwóch prób.
Różne warianty mogą korzystać z odrębnych własnych runnerów; dopuszczenie ich
na konkretnym hoście nadal wymaga zachowania limitów i rezerw zasobów.

Ten dziennik dopuszcza tylko generację. Nie zawiera fitów, ocen, dostępu do
final, zamrożenia wyboru ani zamknięcia pełnej kampanii. Odziedziczone polityki,
wagi końcowych seedów i nieznane budżety legacy zachowują kontekst docelowego
portfolio; nie oznaczają wykonania tych seedów w małym profilu. To audyt
współpracujących lokalnych wykonawców, a nie dowód braku innych sesji lub
globalnie świeżego holdoutu.

[Dowód 09.87](evidence/09-87-development-preparation-execution.json) obejmuje
rzeczywisty dziennik i wrapper uruchomienia z kontrolowanymi wynikami faz,
blokadę nieistniejącego producenta, wszystkie sześć awarii faz i regresję
starej kampanii. Kontrolowane wyniki faz nie są wygenerowanymi danymi Source.
Nie wykonano jeszcze żadnej rzeczywistej generacji 25/50 w tym trybie.

Do zakończenia ścieżki małych profili pozostają rzeczywiste plany scenariuszy
powiązane z natywnymi ziarnami, wykonanie Source i potwierdzenie efektów oraz
krytycznego pokrycia. Osobny wykonawca prób musi jawnie zweryfikować i powiązać
przygotowanych rodziców, zarejestrować ich ponowne odczyty i koszty użycia,
zachować wcześniejszy koszt przygotowania, a potem zweryfikować wybór finalistów
i tę samą decyzję na 50/100. Sam dziennik przygotowania nie daje mu uprawnienia
do treningu. Trwałe checkpointy diagnostyki canonical są osobną ścieżką;
ten wrapper nie deklaruje automatycznego wznowienia przerwanej generacji.

Pełny `make ci-local`, Required CI i publikacja na `origin/main` nadal są
wymagane. Pełny canonical i końcowe portfolio pozostają niezmienione;
AI 09 pozostaje `not_ready`.
