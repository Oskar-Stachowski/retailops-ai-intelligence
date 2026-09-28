# Lokalny serwis diagnostyczny

Serwis uruchamia komenda `retailops-ai serve`; wygodny wariant z katalogu repo:

```bash
make bootstrap
make serve
```

`make serve` jawnie czyta bezpieczny `.env.example`. Własny plik wybierz przez
`make serve ENV_FILE=.env`; zmienne procesu mają pierwszeństwo. Plik `.env`
jest ignorowany przez Git. `Ctrl+C` kończy proces; serwer nie działa w tle.
Port domyślny: **8081**. Nie wymaga AWS, DB ani konta zewnętrznego.

## Endpointy i znaczenie gotowości

| Endpoint | Odpowiedź | Dostęp |
|---|---|---|
| `GET /health` | 200, `status=ok`: proces obsługuje żądania; nie sonduje providerów. | Lokalny |
| `GET /ready` | 200 `ready/degraded` lub 503 problem details z raportem zależności. | Lokalny |
| `GET /version` | Metadane pakietu, opcjonalne build/image, `model=null`. | Lokalny |
| `GET /metrics` | Prometheus; 404, gdy tokenu nie skonfigurowano; 401 przy braku lub błędzie tokenu. | Bearer token + lokalny bind |

W trybie local `HTTP_HOST` dopuszcza tylko `127.0.0.1` albo `::1`.
[Compose](local-stack.md) dopuszcza bind kontenera `0.0.0.0` i Host `api`,
przy zachowaniu portów hosta na loopback. `HTTP_PORT`: 1–65535.
Host żądania musi być `localhost`, `127.0.0.1` lub `[::1]`, z opcjonalnym portem.
Forwarded headers nie zmieniają tożsamości klienta; proxy headers są wyłączone.
Nie ma CORS, Swagger UI, publicznego endpointu OpenAPI ani przekierowania slash.
Rzeczywisty schemat jest wersjonowany w [kontrakcie](../contracts/diagnostics.openapi.json).

Bez DATABASE_URL rola to **foundation**; z DB rola **ai_api** wymaga rzeczywistej
sondy ai_db (wersja migracji, vector i tabela). Foundation wymaga ukończonego startup;
brak bazy/modelu nie jest raportowany jako sprawdzona baza/model. Factory
`create_app` przyjmuje jawny rejestr `Dependency`: nazwa, async check i required.
Warstwa `pipelines/readiness.py` uruchamia kontrole równolegle, z limitem ośmiu
nazw. Każdy check musi być współpracującą operacją async, z własnymi timeoutami
transportu; blokujące SDK należy wyprowadzić poza event loop.

Awaria/wyjątek/timeout wymaganej zależności daje 503. Awaria opcjonalnej daje
`degraded` przy HTTP 200. Wymagalność wynika z funkcji danej roli, więc przyszły
odczyt klasycznych prognoz nie będzie zależał od Bedrock. Zależności nowej roli
trzeba zarejestrować wraz z jej implementacją; obecny serwis nie udaje read API ML.
`READINESS_TIMEOUT_SECONDS` (0.01–5, domyślnie 1) ogranicza cały przebieg,
włącznie z oczekiwaniem w kolejce. Jedna aplikacja wykonuje najwyżej jeden zestaw
sond jednocześnie. Timeout anuluje współpracujące sondy, błędna lub niebooleanowa
odpowiedź nie staje się sukcesem. Nazwy zależności są stałymi identyfikatorami kodu.

## Metryki i token

Token zawiera 32–128 znaków `A–Z a–z 0–9 _ -`. Przykładowe lokalne uruchomienie
z wygenerowanym tokenem w zmiennej procesu (wartość nie jest argumentem serwera):

```bash
export METRICS_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
make serve
```

Klient Prometheus przekazuje `Authorization: Bearer <token>`. Nie umieszczaj
rzeczywistej wartości w Git, argv poleceń ani raportach. Token porównywany jest
w stałym czasie; powielone nagłówki autoryzacji są odrzucane. To lokalna ochrona
telemetrii, nie system tożsamości ani uprawnień do przyszłych danych AI.

Registry jest osobny dla każdej aplikacji. Licznik
`retailops_ai_http_requests_total` i histogram
`retailops_ai_http_request_duration_seconds` mają etykiety method, route, status.
Route jest szablonem frameworka albo `unmatched`; nie zawiera surowej ścieżki,
query, tokenu ani ID żądania. Pomiar kończy się przy zakończeniu odpowiedzi;
scrape zawiera wcześniejsze żądania, a bieżący scrape trafi do kolejnego pomiaru.
Metryki są w pamięci jednego procesu i zerują się po restarcie; brak konfiguracji
wielu workerów, SLO i zewnętrznego monitoringu w tym zakresie.

## Korelacja, ślady i błędy

Poprawny `X-Correlation-ID` jest kanonicznym małym UUID (wersje 1–5);
brak, duplikat lub błędna wartość powoduje wygenerowanie UUID v4.
Nie jest identyfikatorem principal ani dowodem autoryzacji.
OpenTelemetry parsuje `traceparent` W3C; serwer tworzy własny span potomny
albo nowy trace. Odpowiedź zawiera correlation ID i aktywny `traceparent`.
Nie przekazujemy niezweryfikowanego baggage ani tracestate.

`adapters.telemetry.context_headers()` udostępnia bieżący kontekst dla jawnych
wywołań zaufanych providerów. Test HTTP z fałszywym providerem sprawdza przekazanie
kontekstu; testy współbieżności sprawdzają izolację żądań. Nie ma jeszcze
rzeczywistego klienta RetailOps/Bedrock ani eksportera OTLP. Wyłączenie SDK przez
`OTEL_SDK_DISABLED` wyłącza trace; nie wyłącza samego HTTP ani correlation ID.

Serwer zapisuje JSON na stderr: czas, poziom, zdarzenie, szablon route, status,
czas wykonania, correlation ID oraz trace/span IDs. Rejestruje też
`application_started` i `application_stopped`. `LOG_LEVEL` steruje poziomem;
ustawienie WARNING/ERROR ogranicza logi poprawnych żądań. Domyślne access logs
Uvicorn są wyłączone. Formatter nie serializuje dowolnych wiadomości bibliotek,
wyjątków, stack trace, payloadów, query, nagłówków ani ścieżek artefaktów.

Błędy aplikacji używają `application/problem+json`: type, title, status, detail,
instance i correlation_id. Instance to URN identyfikatora żądania; nie odbija URL.
422 i 500 mają stałe bezpieczne komunikaty. Przy 503 readiness dochodzi raport
nazw i statusów zależności, bez treści wyjątków. Odpowiedzi mają `Cache-Control:
no-store` i `X-Content-Type-Options: nosniff`. Błędy parsera HTTP przed wejściem do
ASGI pozostają zachowaniem Uvicorn, poza kontraktem błędów aplikacji.

## Wersja i warstwy

Wersja pakietu pochodzi z zainstalowanych metadata. `BUILD_COMMIT` i
`IMAGE_DIGEST` są opcjonalnymi, walidowanymi deklaracjami procesu budowania:
nie są automatycznie odczytywane z Git ani zweryfikowanym attestation.
Brak wartości daje null. Brak modelu daje jawne `model=null`.

- `api/`: composition root, HTTP, middleware, kontrakty i mapowanie błędów.
- `domain/`: kontrakt sondy i wyniku, bez FastAPI/provider SDK.
- `pipelines/`: wykonanie kontroli gotowości z timeoutem.
- `adapters/`: logi, Prometheus i kontekst OpenTelemetry.

Próby rzeczywistej DB i restartu opisują [dowody persistence](evidence/01-persistence.md).
Przed udostępnieniem serwisu poza loopback wymagane są transport, granica sieciowa
i uwierzytelnianie właściwe dla nowych funkcji; nie wystarczy zmiana bind address.
