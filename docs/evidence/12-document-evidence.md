# AI 12 — kompletność dowodów dokumentowych

Data: 2026-09-29. Zakres: krok 1, korekta fixtures/etykiet dokumentacji i
reguły wystarczalności dowodów. Branch `ai/12-tools`.

## Zmiana i uzasadnienie

Wcześniejszy golden wymagał `answered` dla sześciu pytań AI 11, choć tekst
fixtures nie zawierał potrzebnych informacji. Serwer uznawał każdy pobrany,
uprawniony dokument za wystarczający i cytował jego pierwsze 240 znaków.
Historyczny Sonnet odrzucił taką odpowiedź w `document-1`; raportu ani kosztu
tej próby nie przepisano.

`typed-facts-v2` wymaga reguły pytania i pokrycia każdej wymaganej informacji
przez konkretny cytat z uprawnionego fragmentu o zgodnym pełnym hashu.
Nieznane pytanie, nietrafne źródło lub brak części dowodów kończy się
`insufficient_evidence` bez modelowej syntezy. Wybranie przez model tylko
części wymaganych faktów nie przechodzi walidacji. Status i uprawnienia
nadal obowiązują przed sprawdzeniem reguł.

Reguły są zaufaną konfiguracją serwera, związaną z config ID. Nie pochodzą
z request body ani decyzji LLM. Cytaty mogą sięgać poza dawny limit początku
fragmentu; wspólne źródło dla dwóch twierdzeń ma jedną referencję cytowania.

## Golden i wyniki offline

[Przegląd etykiet](12-document-label-review.json) zachowuje poprzedni i nowy
hash zestawu, sześć niezmienionych pytań, wymagania i referencje autorstwa.
Nowy `agent-canonical-golden-v2` ma pięć poprawnych odpowiedzi i jeden
przypadek `verified`, który nie wspiera pytania. Health/ready wymaga dwóch
części i zachowuje próbę injection jako niezaufaną treść. Dwa dotychczasowe
negatywne testy cytatów/braku dowodów mają spójne pytania i źródła. Pozostałe
42 przypadki oraz progi są identyczne z poprzednim zestawem.

[Raport offline](12-document-offline.json):

- 50/50 przypadków, 36/36 krytycznych; zero niezaliczonych bramek.
- Groundedness 46/46, cytaty 6/6, pokrycie cytatami 6/6.
- Zero zbędnych wywołań narzędzi i 0 USD; brak połączeń AWS.
- Oracles i skrypty są statyczne; nie powstają z katalogu runtime.
- Celowa zmiana oczekiwanej odpowiedzi nadal powoduje niezaliczenie bramki.

Dodatkowe testy obejmują pełne i częściowe pokrycie, nieznane pytania,
oddzielenie celów, nietrafny dokument o najwyższym score, status verified,
zmienione metadane, niedozwolony cytat, prompt injection, niepełny wybór LLM,
dwa cytaty z jednego fragmentu, konfigurację i niedopuszczalne pola requestu.
[Pakiet wheel](12-document-wheel.json) poza checkoutem zachowuje checksumy
ewaluatora i ładuje prompty v4 oraz konfiguracje bez AWS.

## Pozostała walidacja

Łącznie **1026 testów przeszło**: 1024 w pełnym przebiegu sandbox oraz
dwa testy rzeczywistego HTTP po powtórzeniu poza sandboxem, który blokował
otwarcie portu loopback. Nie zmieniano kodu z powodu tego ograniczenia.
Ruff, mypy (135 plików), osiem grup kontraktów, linki, build pakietu,
konfiguracja Compose i Gitleaks (historia oraz pliki) przechodzą.
[Metadane odbioru](12-document-evidence.json) wiążą raporty i konfiguracje.
Nie powtarzano pełnego odbioru PostgreSQL ani zdalnego CI; brak zmian
w migracjach, schemacie bazy i warstwie persistence.

## Ponowny test modelu i granice

[Propozycja](12-document-smoke-proposal.json) wiąże sześć przypadków dokumentacji
z Sonnet 4.6 i cap 0,35 USD. Właściciel zwiększył łączny budżet serii do
1,50 USD. [Rejestr](12-bedrock-budget.json) zachowuje wcześniejsze koszty
oraz pełny cap wcześniejszej przerwanej próby.

[Rzeczywisty test](12-bedrock-runs/sonnet-4-documents.json): **6/6**,
pięć odpowiedzi i jedna odmowa z powodu braku dowodów, bez napraw.
Wykonano 11 CountTokens i 11 Converse: 52 048 input oraz 4373 output.
Koszt szacowany: **0,2439129 USD**, poniżej cap 0,35 USD. Łączne
szacunki/rezerwy: **1,1722710 USD**, pozostało **0,3277290 USD**.
To rozliczenie według stawek testu, nie faktura AWS. Nie zmieniano kodu,
promptów, etykiet ani konfiguracji podczas wykonania.

Źródła, pin i narzędzia tego odbioru są syntetyczne. Nie wygenerowano nowych
embeddings, nie aktywowano indeksu ani nie zmieniono danych AI 11. Etykiety
pozostają `proposed`, bez niezależnej akceptacji człowieka. Reguły obejmują
jawnie określone pytania; właściwy profil AI 11 ma pustą listę reguł do czasu
przeglądu rzeczywistych źródeł. Ten zakres nie zamyka AI 12 i nie potwierdza
jakości odpowiedzi na dowolne pytania ani integracji z AI 10.
