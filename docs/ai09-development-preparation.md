# Przygotowanie danych development

[Planowanie natywne 09.90](evidence/09-90-native-development-planning.md)
i [przygotowanie wariantów 09.91](evidence/09-91-resolved-development-variants.md)
generują ordinary, pobierają plany scenariuszy z przypiętego Source, ponownie
weryfikują przygotowane dane i wykonują oba oryginalne warianty. Ordinary
nie jest generowane drugi raz. Kolejne kroki zachowują jego tożsamość,
receipts i zmierzone koszty.

Dodatek v36 w [09.99](evidence/09-99-canonical-development-preparation.json)
obsługuje również pełny canonical development: **365 dni, 100 produktów,
5 par sprzedaży i 3 lokalizacje zapasu, seed 42**. Używa oryginalnego
`PortfolioSourceRecipe`; nie rozszerza małych profili 25/50 ani nie przypisuje
im nazwy pełnego profilu. Wspólne walidatory zachowują reguły źródeł,
scenariuszy, czasu, zasobów i pochodzenia. Wszystkie 382 wcześniejsze
kontrakty pozostają niezmienione.

Bootstrap obejmuje jedną generację i jeden odczyt planera. Następny dziennik
obejmuje pełne ponowne sprawdzenie ordinary i dwie generacje wariantów.
Wszystkie pięć kosztów pozostaje rozliczone. Bezpośrednie wywołanie generatora
nadal wymaga ukończonego ponownego sprawdzenia i pozostałego wspólnego czasu.
Odbiór rodziców ponownie sprawdza oryginalne dzienniki i receipts; brak
oryginału blokuje dalsze użycie. Nie powstaje dodatkowa próba po błędzie.

Przeszło **118 kontroli przygotowania i wcześniejszych ścieżek**. Audyt
wykrył możliwość zapisania rozwiniętych parametrów innych niż zamrożone
żądanie canonical. Walidacja odrzuca teraz taki plan przed generowaniem;
trzy kontrole odtworzyły błąd i następnie przeszły po poprawce. Pełny Mypy
obejmuje 773 pliki, a siedem schematów v36 przechodzi sprawdzenie.
**328 regresji źródeł i te same 328 z zainstalowanej paczki** przeszły
bez pominięć. Sprawdzono zgodność bajtów 644 modułów i 20 schematów v33–v36
w źródłach, paczce i instalacji. Wyniki używają jawnie kontrolowanych workerów;
nie dowodzą pełnej generacji Source ani fitów Project.

Pozostają powiązanie tych rodziców z wybranymi wcześniej konfiguracjami
i trzema fitami 100, rzeczywiste dane i modele, komplet trzech zastosowań
oraz odbiór CI i publikacja. Canonical diagnostic pozostaje wyłączony;
limit RAM wynosi **12 GiB**, z rezerwą 1 GiB.

Poniżej zachowano opis wcześniejszego wykonawcy v30, który przyjmuje już
gotowe plany scenariuszy.

Kontrakt [v30](../contracts/evaluation/v30/development_preparation_protocol.schema.json)
umożliwia przygotowanie trzech pełnych wariantów jednego profilu development:
ordinary, demand i physical. Korzysta z tych samych sześciu faz
`generate_campaign_parent`: generation, qualification, export, import, curation
i verify. Nie dodaje własnego generatora ani uproszczonej weryfikacji Source.
Profile zachowują [pełną historię i lokalizacje](ai09-development-profiles.md).

**Otwarta aktualizacja pina Source:** [audyt 09.88](ai09-full-source-scenario-audit.md)
wykrył limit 5000 ziaren. Poprawki producenta uzyskały odbiór opisany
w [09.95](evidence/09-95-paired-truth-local-ci.json), ale aktywne receptury
i workflow konsumenta nadal wymagają spójnego przypięcia zaakceptowanego kodu.
Samo uruchomienie wrappera nie zastępuje tej aktualizacji.

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
