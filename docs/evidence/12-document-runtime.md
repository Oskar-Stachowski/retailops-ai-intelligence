# AI 12 — API, resolver źródła i rzeczywisty RAG

Data: 2026-09-29. Branch `ai/12-tools`. Zakres odebrany lokalnie dla dwóch
pytań dokumentacyjnych; AI 12 pozostaje otwarty.

## Przebieg i wynik

Standardowe `create_app`/`serve` może złożyć runtime z jawnych ustawień.
Nie wstrzykiwano fixture backendu do rzeczywistego odbioru. Żądanie przechodzi
przez auth i planner, resolver zweryfikowanego importu AI 03, trwałe admission,
graf, kwalifikowany PostgreSQL/pgvector, Titan, Sonnet oraz zapis wyniku.
HTTP wykonano przez ASGI TestClient, bez osobnego procesu nasłuchującego
na porcie; PostgreSQL oraz wywołania AWS były rzeczywiste.
Nowa migracja `0010_assistant_token_budget` dopuszcza 19 000 tokenów rezerwacji,
zgodnie z istniejącym budżetem 16 000 wejścia + 3000 wyjścia grafu.

[Źródłowe oczekiwania](../../agent/document-runtime.golden.v1.json) powstały
przed testem, na podstawie dokumentacji. Obejmują oryginalne `rag-03` i `rag-04`
z AI 11. Cytaty, hashe pełnych fragmentów, status i wymagane informacje są
niezależne od rankingu i odpowiedzi modelu. Nie zmieniano etykiet pomiędzy próbami.

[Pierwszy test](12-bedrock-runs/sonnet-5-runtime.json) zaliczył 1/2.
[Zapisany ślad](12-bedrock-runs/sonnet-5-runtime-diagnostic.json) drugiego
żądania pokazuje poprawne pobranie wiedzy, odpowiedź wymagającą naprawy
i jej zablokowanie przez wspólny budżet tokenów. Nie zachowano surowej treści
odrzuconego draftu; sam ślad nie rozstrzyga, które pole wymagało naprawy.
Pierwotny raport, koszt i [konfiguracja](12-bedrock-runs/sonnet-5-runtime-config.json)
pozostają niezmienne.

Runtime przekazuje teraz do modelu sprawdzone fakty i cytowania, zamiast
ponownie wysyłać pełne fragmenty wraz z metadanymi. Pełne wyniki RAG pozostają
wejściem walidatora serwera; nie zmieniono progów, reguł, wymaganych cytatów,
limitów pojedynczego żądania ani odpowiedzi oczekiwanych.

[Końcowy test rzeczywisty](12-bedrock-runs/sonnet-6-runtime.json):

- **2/2** poprawne odpowiedzi, bez napraw; 4 CountTokens, 4 Converse.
- 2 rzeczywiste zapytania Titan, 41 tokenów wejścia embeddings.
- Chat: 17 047 input, 2120 output; szacunek **0,0912351 USD**.
- Obie odpowiedzi mają dokładny wymagany cytat, właściwy status/rewizję,
  brak działań biznesowych oraz zgodny runtime/index ID.
- Obie odpowiedzi i terminalne ślady zapisano w PostgreSQL; odczyt właściciela
  przechodzi, drugi uprawniony operator otrzymuje 404.
- Sprawdzono SQL rezerwację 19 000 tokenów. Odpowiedzi porównano z zapisami DB.

W obu płatnych próbach konfiguracja i kod były niezmienne podczas wykonania.
Późniejsza próba miała mniejszy limit procesu: 0,18 USD na chat. Embeddings
mają osobny limit 4 wywołań / 4000 bajtów. [Rejestr kampanii](12-bedrock-budget.json)
zachowuje 0,005 USD rezerwy embeddings na każdą próbę oraz pełne 0,45 USD
starszego przerwanego testu. Łącznie szacunki/rezerwy wynoszą
**1,4017210 USD**, pozostało **0,0982790 USD** z zatwierdzonych 1,50 USD.
To nie jest rachunek AWS.

## Źródła i zabezpieczenia

Resolver użył zaakceptowanego importu AI 03: source `a12866e1…`,
snapshot `4d856185…`, 20 produktów, 2 lokalizacje i 6 przypisań kanału.
Są to syntetyczne dane źródłowe z przekazanego fixture, nie bieżąca baza
operacyjna. Importer weryfikuje całość, a resolver ponownie porównuje bajty
wybranych plików z manifestem przed odczytem. Nie korzysta z truth ani aliasów legacy.

RAG użył kwalifikowanego indeksu `d193c015…`, 451 rzeczywistych fragmentów,
Titan V2 1024 i źródła `docs/access-control.md` z rewizji `082bed47…`.
Nie zmieniano korpusu, kwalifikacji, filtrów ani aktywnego indeksu.
Źródłowa dokumentacja nie jest deklaracją stanu obecnego `main`.

[Preflight](12-document-runtime-preflight.json) i
[preflight końcowego profilu](12-document-runtime-projection-preflight.json)
sprawdzają PostgreSQL retrieval na wcześniej zapisanych wektorach Titan,
bez nowych wywołań AWS. 401/403/422 i rozpoznana odmowa zatrzymują żądania
bez providera. Nieznane lub nieuprawnione ID, zły kanał, luki i niejednoznaczne
przypisania oraz zmieniony pin są dodatkowo objęte testami negatywnymi.
SQL przyjmuje 19 000 i odrzuca 19 001 tokenów.

Grant dokumentacyjny wymaga jawnego scope przy `assistant:query`, bez
nadawania praw do sprzedaży. Zmianę tego kontraktu sprawdzono również
w testach narzędzi. Leniwa inicjalizacja AWS jest wspólna dla współbieżnych
żądań i pozostaje pojedyncza po anulowaniu oczekującego klienta.

## Kontrole i stan środowiska

[Golden offline](12-document-runtime-offline.json): **50/50**, krytyczne
**36/36**, wszystkie bramki przechodzą. Pozostałe kontrole: Ruff, mypy,
osiem grup kontraktów, handoff/import/curated AI 03, build wheel/sdist,
linki oraz konfiguracja Compose. [Wheel poza checkoutem](12-document-runtime-wheel.json)
zachowuje checksum kodu i katalogu źródłowego, ładuje profil i nie wywołuje AWS.
Końcowy pełny przebieg: **1150/1150 testów**, z dozwolonym loopback,
bez wywołań AWS. Skan historii i katalogu Gitleaks nie znalazł sekretów.
[Manifest odbioru](12-document-runtime.json) wiąże wyniki i artefakty.

[Porządkowanie bazy](12-document-runtime-cleanup.json) zachowuje prywatną
kopię danych testowych, przywraca schemat `0008_rag_semantic` używany przez
główny checkout i potwierdza identyczny pin RAG. Własne rekordy testowe usunięto
po backupie. Stary constraint prawidłowo uniemożliwiał obniżenie schematu,
dopóki istniały rezerwacje 19 000 tokenów. Kontener przywrócono do pierwotnej
konfiguracji i zatrzymano; runtime nie jest pozostawiony jako działający serwis.
Do uruchomienia branchu AI 12 trzeba jawnie wykonać jego migracje.
Nie wykonywano push ani zdalnego CI.

## Dalsze bramki

Nie jest to pełny golden rzeczywistego modelu/retrieval ani ogólny planner
języka naturalnego. Następny zakres obejmuje więcej pytań, pokrycie pełnego
golden i ocenę odpowiedzi na rzeczywistych źródłach. Narzędzia biznesowe/ML
oraz AI 10 i E2E sugestii przez outbox/v2/read API/UI pozostają do podłączenia.
Etykiety i profil mają lokalny przegląd deweloperski, bez niezależnego odbioru
business/model. Limity procesu nie są wspólnym budżetem wielu restartów/replik.
