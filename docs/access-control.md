# Lokalna tożsamość i uprawnienia API

**2026-09-28 · działająca granica lokalnego API etapu 01.**
[Dowody](evidence/01-access.md), [schemas i OpenAPI](../contracts/access/v1/access.openapi.json).
Serwer weryfikuje opaque Bearer token i przypisuje principal z prywatnej mapy.
Nie używa user_id, roli z body/query/header ani demo-admin RetailOps jako loginu.

## Endpointy

| Endpoint | Uprawnienie i wynik |
|---|---|
| `GET /api/v1/identity` | Zweryfikowany token; własny principal, role, capabilities i skonfigurowany scope |
| `POST /api/v1/access/forecast-check` | Jawne forecast:read i cały dozwolony scope; 200 z decyzją dla tego żądania |
| `GET /api/v1/admin/access-policy` | Jawne access:admin; wyłącznie policy ID i liczba principal/credentials |

Forecast-check jest **preflight uprawnień**; nie zwraca prognozy, nie sprawdza
istnienia produktu w źródle i nie potwierdza gotowości modelu.
[Kontrakt danych](data-contracts.md) oraz późniejszy importer/serving mają własne bramki.
Schematy policy/grants i przykłady HTTP mają wersję 1.0, bez unknown fields.
[Przykład żądania](../contracts/access/v1/forecast-check.v1.example.json).

Brak/niepoprawny, expired, future lub revoked token → 401 z WWW-Authenticate: Bearer.
Brak capability albo choć jedna nieautoryzowana jednostka scope → 403.
Nie filtrujemy po cichu niedozwolonych jednostek. Cały requested scope musi się
mieścić w server grant; klient nie nadaje principal, roles ani capabilities.
Zduplikowane nagłówki Authorization, cookie, query token i forwarded identity
nie są poświadczeniami. Nie ma login/password endpointu.

Role viewer/operator/admin są metadanymi polityki, bez automatycznej hierarchii.
Capabilities nadaje się jawnie. Access:admin wymaga roli admin; sam admin nie
otrzymuje forecast:read. Każda forecast capability wymaga niepustego scope.
Mapa scope jest iloczynem jawnych product IDs × selling locations × channels
danego principal. Nie ma wildcardów, nieograniczonego default ani tenant resolvera.
Żądanie wymaga scope; maksymalnie 20 produktów i 5 lokalizacji, jeden kanał.
POST pod /api/v1 ma limit 16 KiB oraz 5 sekund odbioru body (413/408); duplicate
JSON keys i NaN/Infinity są odrzucane. Nie jest to limiter liczby żądań.

## Uruchomienie lokalne

Z katalogu repo; najpierw dostosuj i przejrzyj grant template do własnego scope.
Przykład używa ilustracyjnych p-101/s-03 i dwóch różnych principal viewer/admin.
Nie daje praw do wszystkich danych ani nie jest kontem RetailOps.

```bash
make bootstrap
mkdir -p .local
uv run --locked retailops-ai access-init \
  --grants-file contracts/access/v1/grant-template.v1.example.json \
  --output-dir .local/access --ttl-hours 8
export API_AUTH_FILE=.local/access/api-access-policy.json
uv run --locked retailops-ai config-check --env-file .env.example
make serve ENV_FILE=.env.example
```

Initializer działa offline, generuje osobny losowy token dla każdego principal
(32 bajty z secrets), tworzy nowy katalog 0700 i dwa pliki 0600:
`api-access-policy.json` ma tylko SHA-256 fingerprints i grants,
`api-client-credentials.json` zawiera prywatne tokeny klienta.
Nie wypisuje ich na stdout, nie nadpisuje istniejącego katalogu.
TTL 1–24 godziny, domyślnie 8. Zwykłe hasło użytkownika nie jest zamiennikiem
generowanego tokenu. Pliki są ignorowane przez Git i poza kontekstem Docker builda.
Przed pierwszym użyciem zmień template zgodnie z rzeczywistym zamierzonym dostępem.

Klient odczytuje swój token z prywatnego pliku i przekazuje go w Authorization.
Przykład nie umieszcza tokenu w argv, URL ani output:

```python
import json
from pathlib import Path
from urllib.request import Request, urlopen

entries = json.loads(Path(".local/access/api-client-credentials.json").read_text())["credentials"]
token = next(row["bearer_token"] for row in entries if row["principal_id"] == "local-viewer")
request = Request(
    "http://127.0.0.1:8081/api/v1/identity", headers={"Authorization": "Bearer " + token}
)
with urlopen(request, timeout=5) as response:
    print(response.status)  # only the status
```

API_AUTH_FILE jest opcjonalny: bez niego wszystkie zarejestrowane endpointy
aplikacyjne odmawiają dostępu, diagnostyka nadal działa.
Jawny plik brakujący/uszkodzony, symlink, inne UID, otwarte uprawnienia lub
przekroczenie 128 KiB blokują config-check/start z bezpiecznym kodem błędu.
Loader dopuszcza tylko zwykły plik właściciela procesu, bez group/other bits.
Credentials mają unikalne fingerprints i znany principal. Lifetime ≤24 h.
SHA-256 porównuje się przez compare_digest, bez zwracania wartości credential.

Metryki mają osobny METRICS_TOKEN. Poświadczenie API nie otwiera /metrics;
token metryk nie tworzy principal. Konfiguracja odmawia reuse tego samego tokenu.
Paths, hashes, tokeny, body i niezweryfikowane claims nie trafiają do logów/metryk.
Błędy używają stałych problem details; correlation ID nie jest principal.

## Zmiana uprawnień i granice

Polityka jest snapshotem w pamięci procesu, ładowanym przy starcie.
Po zmianie grants, revoked albo rotation **zrestartuj każdy proces API**.
Wygaśnięcie jest sprawdzane dla każdego żądania, bez restartu; dokładna granica
expires_at jest niedostępna. Nadanie nowych tokenów wymaga nowego prywatnego
katalogu i jawnego przełączenia API_AUTH_FILE. Nie ma HTTP edycji polityki ani
automatycznej rotacji/hot reload. Nie usuwaj starych plików jako sposobu odwołania
już działającego snapshotu.

Bieżący Compose nie montuje polityki aplikacyjnej: jego /api/v1 pozostaje zamknięte.
Odbiór poświadczeń dotyczy lokalnego serve z plikiem należącym do jego UID.
Integracja polityki/secret managera z kontenerami wymaga poprawnego ownership
dla UID 10001, read-only mount i własnego odbioru; nie zmieniaj uprawnień na 0644.
DB/MLflow, migracje i metryki Compose pozostają dotychczasowym lokalnym stosem.
MLflow nie uzyskał aplikacyjnego auth przez tę zmianę.

Nie ma OIDC/JWT, MFA, publicznego TLS, tenant isolation, trwałego audit store,
rate limiter, chronionego serving API lub tool executora. Warstwa domenowa
principal/scope jest wspólna dla przyszłych odczytów; każdy kolejny endpoint
musi ją egzekwować także dla listy, pojedynczego rekordu i cache.

Źródła implementacyjne: [FastAPI security](https://fastapi.tiangolo.com/reference/security/),
[Python secrets](https://docs.python.org/3.11/library/secrets.html),
[Python os](https://docs.python.org/3.11/library/os.html).

