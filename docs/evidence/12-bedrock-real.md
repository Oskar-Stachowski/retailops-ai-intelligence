# AI 12 — rzeczywiste próby Bedrock

Data: **2026-09-29**. [Metadane](12-bedrock-real.json),
[budżet wszystkich prób](12-bedrock-budget.json),
[aktualna instrukcja](../agent-bedrock.md).

## Wynik

**Sonnet 4.6: 5/6, status failed. AI 12 pozostaje otwarty.**
[Końcowy raport](12-bedrock-runs/sonnet-3.json) obejmuje 10 rzeczywistych
wywołań Converse, 44 197 input i 3911 output tokens, 0,2103816 USD szacowanego
kosztu. Dwie naprawy dotyczą dwóch odrębnych pytań i mieszczą się w limicie
jednej naprawy na pytanie. Dwa przypadki bezpieczeństwa nie używają modelu.

| Przypadek | Wynik ostatniego testu |
| --- | --- |
| Sprzedaż | passed |
| Zapas | passed |
| Porównanie okresów | passed |
| Dokumentacja | failed / invalid_evidence |
| Odmowa złożenia zamówienia | passed, serwer |
| Cudzy scope | passed, serwer |

Model działa przez zweryfikowany profil `eu.anthropic.claude-sonnet-4-6`
z `eu-central-1`. Dostęp i ceny sprawdzono w AWS. Narzędzia oraz retrieval są
syntetycznymi fixtures. To test transportu, planu i zgodności z kanonicznymi
dowodami; nie jest pomiarem pełnego użytkowego RAG ani odczytu rzeczywistych ML.

W [wcześniejszej próbie Haiku](12-bedrock-runs/haiku-4.json) żaden z czterech
przypadków wymagających modelu nie przeszedł; przeszły tylko dwa zabezpieczenia
serwera. [Wcześniejszy Sonnet](12-bedrock-runs/sonnet-1.json) przeszedł sprzedaż
i zapas przy tych samych pierwotnych limitach. Sonnet jest lepszym kandydatem
do dalszej kwalifikacji na podstawie tej małej próby. Nie dowodzi to ogólnej
przewagi jakości: końcowy test ma również większy budżet tokenów.

## Otwarty problem dokumentacji

Pytanie `document-1` brzmi „Jak rozpocząć pracę lokalną nad pakietem AI?”.
Zamrożony dokument zawiera tylko informację, że jest syntetycznym przykładem
niepotwierdzającym wdrożenia. Nie wyjaśnia uruchomienia pakietu. Model
skopiował cytat, ale zwrócił `insufficient_evidence`, niższą pewność i własne
ograniczenie. Serwerowy katalog/oracle wymaga `answered`, `medium` i pustych
limitations, dlatego walidacja odrzuciła odpowiedź.

To ujawnia ograniczenie semantyczne fixture i katalogu: obecność dokumentu
nie wystarcza, by uznać go za odpowiedź na pytanie. Nie należy naprawiać tego
przez wymuszanie odpowiedzi bez właściwego źródła. Przed kwalifikacją trzeba
przygotować odpowiednie dowody dla pytań, niezależnie przejrzeć oracles oraz
sprawdzić osobne przypadki braku odpowiedzi. W tej serii **nie zmieniono golden,
etykiet, progów ani reguł walidatora**, by dopasować je do modelu.

## Wykonany zakres techniczny

Adapter sprawdza profil EU i ten sam model bazowy dla CountTokens/Converse.
CLI rozpoznaje brak formularza, nieaktywną umowę, brak uprawnień lub nieznany
status przed inference. Status nie zawiera formularza ani surowych błędów AWS.

Rzeczywisty Haiku pokazał CountTokens=4908 i usage input=4891. Rozliczenie
akceptuje dodatni input do wysokości rezerwy, przy prawidłowej sumie i output
w limicie. Odpowiedzi większe od rezerwy i niewiarygodny usage zachowują pełny
debit. Dodano ograniczoną diagnostykę kodów/liczników, bez treści promptów.

Realne profile mają 16 000 input i 3000 output na pytanie, maksymalnie 1500
output na wywołanie. Pozostały skończony graf, jedna naprawa, limity narzędzi,
czasu i kosztu. Schematy trace/HTTP/ewaluacji opisują te same górne granice;
profile fake zachowują swoje wcześniejsze limity.

## Rozliczenie

Zgoda użytkownika: **1,00 USD łącznie** na serię. Rejestr zawiera dziewięć prób
oraz niezmienione raporty, także failed i checkpoint po przerwaniu.
Łączne szacunki/rezerwy: **0,9283581 USD**, pozostało **0,0716419 USD**.
To konserwatywne rozliczenie testu, nie faktura AWS.

Próba `sonnet-2` została unieważniona przez zmianę wiązanej konfiguracji w
czasie wykonywania diagnostyki. Nie ma raportu końcowego; zachowano pełne
**0,45 USD** rezerwy i nie uznano jej za wynik jakości. Kolejny pełny test
z cap 0,25 USD nie mieści się już w pozostałej kwocie. Nowych wywołań w tej
serii nie zaplanowano. Oddzielne procesy nie mogą pomijać rejestru kosztów.

## Walidacja i pozostały zakres

**1003/1003 testy**, mypy (134 pliki), Ruff, osiem zestawów snapshotów kontraktów,
linki dokumentacji, konfiguracja Compose i Gitleaks (pliki/historia) przechodzą.
[Golden offline](12-bedrock-real-golden.json): **50/50**, krytyczne **36/36**.
Nie powtarzano pełnego odbioru persistence ani zdalnego CI w tym zakresie.


[Weryfikacja pakietu](12-bedrock-real-wheel.json) ładuje wheel poza checkoutem
i sprawdza wiązanie ewaluatora oraz obu konfiguracji, bez AWS. Pełne wyniki
regresji, kontraktów, golden i kontroli sekretów są zapisane w
[metadanych odbioru](12-bedrock-real.json).

Dalej: semantyczna poprawa zestawu dokumentacji, planner pytań/resolver source
IDs, podłączenie Assistant API do RAG, adaptery rzeczywistych źródeł i pełna
ewaluacja modelu/retrieval. AI 10 oraz E2E sugestia/outbox/v2/read API/UI
pozostają wymagane do zamknięcia etapu. Nie wykonano push ani wdrożenia.
