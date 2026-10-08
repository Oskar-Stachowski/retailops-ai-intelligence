# Assistant API i trwałe wyniki

[Kontrakty](../contracts/assistant/v1/assistant.openapi.json) obejmują queries,
safe runs i chronione odczyty utrwalonych rekomendacji. HTTP działa z grafem
na fixtures oraz rzeczywistym PostgreSQL. [Runtime natywny](assistant-native-bedrock.md)
podłącza osiem adapterów i rzeczywisty indeks AI11; jego kwalifikacja LLM
pozostaje zatrzymana. Bez runtime autoryzowane query zwraca 503, ale odczyty
już zapisanych wyników nadal korzystają z trwałego store.

## Żądanie i odpowiedź

`POST /api/v1/assistant/queries` wymaga prywatnego bearer, roli `operator`,
`assistant:query` i praw do całego wskazanego zakresu oraz użytych narzędzi.
Caller podaje tylko `question`, `scope` i opcjonalne `conversation_id: null`.
Przykład: [query](../contracts/assistant/v1/query.v1.example.json).

- Do 2000 znaków / 8000 bajtów pytania, 20 produktów, 5 sklepów, 90 dni.
- Canonical UUID produktów/sklepów, ISO daty, bez duplikatów. `from <= to`.
- MVP nie ma historii konwersacji. Żądanie nie wybiera principal, intent,
  channel, modelu, promptu, pinu ani narzędzi.
- Globalna granica `/api/v1` wymaga JSON, odrzuca powtórzone klucze/NaN,
  ogranicza body do 16 KiB i czas jego odbioru do 5 s.

`GraphAssistant` wymaga serwerowego planner/resolvera i fabryki świeżego grafu.
Resolver musi sprawdzić istnienie identyfikatorów w źródle. W tym profilu
`store_id` jest kanonicznym source ID lokalizacji sprzedaży; API nie tworzy
aliasów ani nie zgaduje mapowania. Zwrócony graph request musi zachować dokładne
IDs, pytanie i okres. Channel jest jawnie rozstrzygnięty na serwerze i sprawdzony
w grants. Nie ma ogólnego klasyfikatora języka naturalnego w tej warstwie.
Profil dokumentacyjny ma dwie jawne trasy pytań i resolver importu AI 03.

200 zawiera wszystkie pola MVP: answer/trace UUID, outcome, summary, evidence,
recommended_actions, confidence, freshness, citations, limitations,
agent_config_version, index_id i created_at. Nie zawiera wewnętrznego `kind`.
Akcje mają trwałe recommendation UUID i wymagają przeglądu człowieka.
Te same candidate/trace tworzą tę samą identity UUIDv5; nowy HTTP request jest
nowym runem. Publiczne API udostępnia odczyt. Publikację trwałej sugestii kontroluje
serwerowy [outbox](assistant-suggestion-outbox.md); nie nadaje prawa do zapisu workflow RetailOps.

401/403 oznacza tożsamość/uprawnienia; 422 schema/zakres; 424 wymaganą zależność
danych; 429 admission lub wyczerpany budżet; 502 niewalidowalny output;
503 brak chat/storage/configuration; 504 deadline. Wszystkie błędy mają
problem-details i correlation UUID, bez wyjątków, pytań, sekretów lub hostów.
424 ma `urn:retailops:problem:dependency-unavailable` i stały opis klasy źródła.

## Odczyt śladu

`GET /api/v1/assistant/runs/{trace_id}` zwraca status running/succeeded/failed,
answer/outcome, czasy, wersje, nodes, tools, usage i error_code. Brak CoT,
pełnych payloadów narzędzi, pytania i treści odpowiedzi. Właściciel nadal musi
mieć operator/assistant:query, cały pierwotny scope, prawa użytych narzędzi
i zachowane uprawnienia wiedzy, gdy run używał RAG. Cofnięcie tych praw ukrywa
trace. Brak zasobu i cudzy/niedozwolony trace zwracają takie samo 404.

Administrator wymaga osobnego `assistant:audit`; sama rola admin lub
`access:admin` nie daje tego prawa ani prawa do query. Policy rotation nadal
wymaga restartu wszystkich procesów, zgodnie z [lokalnym auth](access-control.md).

## Odczyt rekomendacji

`GET /api/v1/recommendations` zwraca `{items, next_offset}`. Domyślny limit wynosi
50, maksymalny 100; offset ma zakres 0–500. Paginacja następuje po kontroli
uprawnień i nie liczy cudzych lub niedostępnych rekordów. Powtórzone i nieznane
parametry dają 422. `GET /api/v1/recommendations/{recommendation_id}` zwraca
pełny immutable [persisted item](../contracts/assistant/v1/suggestion.v1.schema.json).

Operator wymaga `assistant:query`, własności runu, całego pierwotnego zakresu,
zachowanych praw narzędzi/RAG oraz osobnego fizycznego grantu, gdy sugestia
wskazuje magazyn. Administrator wymaga `assistant:audit`. Brak, cudzy wpis,
cofnięte prawa i wygaśnięcie dają jednakowe 404; lista je pomija. Wygaśnięcie
obowiązuje także administratora. Odczyt nie oznacza ponownej kwalifikacji danych
ani bieżącego wdrożenia zapisanej wersji modelu.

Zapytania używają transakcji SQL `READ ONLY`, timeoutu 2 s i ograniczonej liczby
rekordów. Nie uruchamiają cleanup, modelu, enqueue ani publikacji. Immutable
`status=proposed` i `requires_human_review=true` pozostają zapisane bez zmiany.
Aktualną realizację lub zmianę źródła/model release ocenia się ponownie przez
nowy run i dotychczasowy workflow człowieka; API nie wykonuje rekomendacji.

## Zapis i admission

Migracja `0009_assistant` tworzy w odrębnej bazie AI `assistant_runs`,
`assistant_answers`, `assistant_suggestions` i wiązanie polityki admission.
Migracja `0010_assistant_token_budget` dostosowuje limit SQL do 19 000 tokenów
(16 000 wejścia + 3000 wyjścia); limity okna pozostają takie same.
Run ma właściciela, scope, wymagane prawa, hash requestu, HTTP correlation UUID
i claim. Pytanie/payloady narzędzi nie trafiają do trace. Odpowiedź i pełny
review candidate są chronionymi danymi własnymi AI; nie są rekordem workflow
ani potwierdzeniem delivery w Source. Włączenie outboxa dodaje atomowy zapis
eventu; osobny worker wymaga rzeczywistego ACK i deduplikacji Source.

Admission zapisuje running przed grafem. Jedna transakcja zapisuje odpowiedź,
kandydatów i terminalny trace. FK, constraints i kontrola przejść blokują
niezgodne powiązania, ponowne zakończenie i mutację wyniku. Błąd zapisu
wycofuje całą transakcję. Run pozostaje running do wygaśnięcia lease i otrzymuje
failed/deadline_exceeded przy kolejnym admission/odczycie. Późny proces ma
nieaktualny claim; nie może zapisać odpowiedzi.

[Polityka](../contracts/assistant/v1/admission-policy.v1.example.json) ma
domyślnie 2 równoległe runy principal, 8 łącznie, 6/24 runy w ruchomym oknie
60 s, rezerwacje 81 000/324 000 tokenów oraz 1/4 USD per principal/łącznie.
Kwoty okna są ograniczeniami konfiguracji, nie cennikiem. Per-run rezerwacja
pochodzi z wersjonowanego budżetu modelu, nie z requestu ani oceny LLM.
Cały maksymalny input/output i max_run_cost są odliczane przy admission,
bez zwrotu po sukcesie, błędzie lub awarii procesu, aż minie okno.
To konserwatywna rezerwacja, nie rachunek AWS ani dokładny pomiar zużycia.
Usage opisuje zaobserwowane wykonanie; anulowany krok może nie zwrócić usage.

Transakcyjny lock i zegar PostgreSQL obejmują wszystkie procesy/repliki tego
środowiska. Pierwszy zapis wiąże politykę; odmienne limity w innym procesie
dają 503 zamiast obchodzenia budżetu. Zmiana utrwalonej polityki wymaga
kontrolowanej zmiany administracyjnej; brak endpointu LLM do tej operacji.
Globalny execution deadline obejmuje resolver, admission, graf i completion;
nie przekracza 45 s. Lease ma deadline i jest sprawdzany przy completion.
Runy/wyniki mają retencję 900 s i capacity 100 na środowisko. Cleanup przy
admission/odczycie usuwa wygasłe rekordy z wynikami przez CASCADE; brak
samodzielnego workera retencji. Przegląd sugestii sprawdza ich krótsze expiry;
sam zapis historycznego candidate nie oznacza, że nadal można go użyć.

## Uruchomienie i dalsze podłączenie

`create_app(..., assistant_backend=...)` jest punktem kompozycji. Runtime musi
otrzymać prawdziwy planner/resolver, oceniony graph config, provider i dane.
Przy DATABASE_URL store PostgreSQL jest składany automatycznie. Wstrzykiwany
store oraz fixture backend są dostępne tylko w `APP_ENV=test`. Brak runtime
nie uruchamia fake. Awaria assistant nie zmienia health ani read API ML/RAG.

`make contracts-check` kontroluje Assistant schemas/OpenAPI.
`make agent-security-test` obejmuje HTTP i kontrolę cofniętych grants.
`make compose-smoke` uruchamia odizolowany scripted backend w osobnym procesie,
sprawdza rzeczywisty HTTP/PG, równoległe admission, token/cost debit,
fault injection, SIGKILL API i zachowanie trzech wyników po restarcie bazy.
Fixtures mierzą te mechanizmy; nie potwierdzają jakości LLM lub danych ML.

[Runtime Bedrock](assistant-native-bedrock.md), natywne adaptery i ścieżka
sugestia → outbox/v2 → Source API/UI są zaimplementowane. Osobny test bez AWS
sprawdza mechanikę integracji przy jawnym fixture LLM i obserwacji testowej.
[Punkt wznowienia](ai12-paid-qualification.md) opisuje niezależny przegląd etykiet,
bieżące piny źródeł/modeli i przyszłą kwalifikację realnego Sonnet/Titan.
