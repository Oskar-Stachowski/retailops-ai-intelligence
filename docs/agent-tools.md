# AI 12 — narzędzia agenta

Pierwszy zakres AI 12: typowany katalog i wykonawca narzędzi. Można go rozwijać
równolegle z AI 03 w osobnym branchu/worktree. [Status](STATUS.md) wskazuje
otwarte zakresy; [odbiór](evidence/12-tools.md) podaje testy i granice.

## Katalog i źródła

| Narzędzie | Jawne uprawnienie | Stan adaptera |
|---|---|---|
| `get_sales_summary` | `sales:read` | Ścisły kontrakt i fixture; rzeczywisty approved snapshot/adapter po etapach danych/integracji. |
| `get_inventory_status` | `inventory:read` | Ścisły kontrakt i fixture; rzeczywisty mapping i inventory po AI 06/10. |
| `get_demand_forecast` | `forecast:read` | Adapter [natywnego readera v12](agent-forecast-v12.md); pełny kontrakt publikacji, odczyt read-only i osobna kwalifikacja integracji runtime. Historyczny PredictionRecord/fixture zachowany. |
| `get_stockout_risk` | `stockout:read` | Ścisły kontrakt i fixture; kalibrowany model po AI 08/10. |
| `get_detected_anomalies` | `anomalies:read` | Ścisły kontrakt i fixture; detektor po AI 07/10. |
| `get_live_operations` | `operations:read` | Ścisły kontrakt i fixture; typowany read model po AI 10. |
| `get_model_status` | `model:read` | Ścisły kontrakt i fixture; wersja wdrożona i release po AI 05/10. |
| `search_knowledge` | `knowledge:read` | Adapter `PinnedKnowledgeTool` do `PostgresKnowledge.search_pinned` z AI 11. |

Wspólną bramką wykonania jest rola `operator` oraz jawna capability
`assistant:query`. Grant źródła danych wymaga `scope`; odczyt wiedzy wymaga
`knowledge_scope`. Admin otrzymuje te prawa tylko przez osobne nadanie.
Prywatne pliki i weryfikację tokenów opisują [uprawnienia](access-control.md).

Model wybiera wyłącznie nazwę z ośmiu narzędzi i argumenty danego schematu.
Nie wybiera principal, roli, tokenu, ścieżki, URL, SQL ani indeksu. Principal
pochodzi z `LocalAccess.authenticate` podczas otwierania sesji serwerowej.
JSON odrzuca dodatkowe/zdublowane pola i liczby niefinitywne.

## Scope, czas i wyniki

Cały żądany zakres musi należeć do grantu; nieautoryzowany produkt, lokalizacja
lub kanał odrzuca wywołanie przed adapterem. Nullable scope narzędzi danych
wymaga jawnego, ograniczonego defaultu przekazanego przez serwer. Brak defaultu
zwraca `invalid_scope`. Forecast zachowuje istniejący jawny scope.

Identyfikatory `selling_location_id` są identyfikatorami źródła. Adapter biznesowy
musi potwierdzić grain i mapping; nadanie poświadczenia nie potwierdza istnienia
produktu/sklepu ani dostępności filtrowania w źródle. `InventoryItem` wymaga
`stock_location_id` i `mapping_ref`; jednostki physical/reserved/available muszą
się uzgadniać. Model status wymaga wdrożonego release i evidence oceny.

`ToolPolicy` pozwala tylko zaostrzyć limity: 6 wywołań, 45 s całej sesji,
5 s oczekiwania na narzędzie, 90 dni, 20 produktów, 5 lokalizacji i 50 wierszy.
Default limitu odpowiedzi to 20. Jedna sesja ma wspólny budżet także przy
równoległych wywołaniach. Timeout, błąd i anulowanie zużywają próbę; nie ma
automatycznego retry ani resetowania deadline. [Rozmowa](agent-chat.md) dodaje
budżety modelu/tokenów/kosztu, a [Assistant API](assistant-api.md) wspólne admission.

Executor ponownie waliduje wynik adaptera: typ, źródło, ilość bajtów, limit,
unikalne rekordy, cały scope i dokładny okres. Dane przyszłe lub stale oraz
błędne powiązania nie trafiają do kolejnego kroku. Freshness danych jest
liczona względem żądanego `as_of` i wersjonowanego limitu sekund; narzędzia
użytkowe wymagają później polityk dopasowanych do ich rzeczywistych źródeł.
Forecast ponownie stosuje istniejące kontrole request/lineage i odrzuca
`stale/unknown`. [Natywny wynik v12](agent-forecast-v12.md) zachowuje publikację
bez konwersji do ModelRecord; rozdziela 24 h wieku origin od 300 s wieku
sprawdzonego widoku. Wynik `no_data` danych nowych narzędzi ma ref do sprawdzonego
źródła i as-of; brak adaptera/source daje `unavailable`, a nie zerowe wartości.

Wyszukiwanie otrzymuje jedną `IndexPin` rozwiązaną przez serwer na początku runu.
Pin musi być z kanału `retrieval`, tego środowiska i rzeczywistej przestrzeni
Bedrock. Backend z AI 11 ponownie sprawdza kwalifikację i live deny przed
wynikiem. Executor sprawdza index/generation, chunk IDs, status/claim kind,
uprawnienia i budżet kontekstu. Zwrócony materiał ma `untrusted_reference`.

Timeout przerywa oczekiwanie agenta; adapter synchroniczny uruchomiony w wątku
kończy pracę zgodnie z własnymi timeoutami SQL/SDK z AI 11. Nie rozpoczynamy
retry po upływie budżetu. Admission Assistant API ogranicza aktywne runy
w całym środowisku; adapter zachowuje własne timeouty I/O.

## Inspekcja i weryfikacja

```bash
uv run --locked retailops-ai agent-tools
uv run --locked retailops-ai agent-tool-check request contracts/agent/v1/get_sales_summary.request.v1.example.json
uv run --locked retailops-ai agent-tool-check result contracts/agent/v1/get_sales_summary.result.v1.example.json
uv run --locked retailops-ai agent-tool-check policy contracts/agent/v1/tool-policy.v1.example.json
uv run --locked pytest tests/test_agent_tools.py
make contracts-check
```

CLI tylko wyświetla katalog lub waliduje lokalny plik. Kontrakty i przykłady są
w [catalogue.json](../contracts/agent/v1/catalogue.json). Przykłady to syntetyczne
scenariusze; fixture forecast zmienia jedynie testową freshness i zachowuje
oryginalne intelligence examples bez zmian. `FixtureTools` wiąże odpowiedź
z hashem całego żądania; niepasujący przypadek jest `unavailable`.
Fixtures wymagają `allow_fixtures=True` oraz środowiska `test`.

Minimalny kod serwerowy, po bezpiecznym uzyskaniu tokenu i rozwiązaniu pinu:

```python
executor = ToolExecutor(
    authority,
    {"search_knowledge": PinnedKnowledgeTool(postgres_backend)},
    tool_policy,
    "local",
)
session = executor.open_session(authorization_header, pin=server_resolved_pin)
result = await session.execute_json(validated_tool_call_json)
```

`ToolFailure.error` ma stały kod, opis i `retryable`; wyjątek adaptera nie jest
kopiowany. Audit zapisuje nazwę z katalogu, status, duration, error code oraz
bezpieczne source refs. Pytanie, scope, token, payload i tok rozumowania nie są
częścią tego zapisu. Trwały, autoryzowany trace opisuje [Assistant API](assistant-api.md).

## Następny zakres

[Konfiguracja i fake chat](agent-chat.md), [ograniczony graf](agent-graph.md)
oraz [reguły sugestii i golden fixtures](agent-evaluation.md) mają osobne
odbiory. [Assistant API](assistant-api.md) utrwala wyniki w bazie AI.
Następne bramki podaje [status](STATUS.md): rzeczywisty chat i narzędzia biznesowe,
kwalifikacja realnego modelu oraz integracja sugestii z AI 10.
