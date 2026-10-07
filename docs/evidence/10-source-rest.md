# AI 10 — odbiór przyrostu bounded source REST

Data: 2026-10-04. Cały AI 10: **in_progress**.
Implementację i kroki operatora opisuje [runbook](../source-rest-v2.md).

Pin source OpenAPI pochodzi z commitu
`271ad966b32879dc4ef583b6bee1abf19ae0c1cf` RetailOps, z gałęzi
`ai/10-source-read-gateway`, bazującej na `a6f7067` pierwszego przyrostu.
Gałąź klienta AI `ai/10-source-rest` bazuje na `8406ff2`.
Dokładne SHA/digest są w `retailops_ai/source_rest/upstream.json`.
Pin oznacza właściciela kontraktu; runtime jest sprawdzany przez jego digest.

| Kontrola lokalna | Wynik i zakres |
|---|---|
| Klient przez rzeczywisty localhost HTTP | 33/33 passed: 0/1/100/125 rows, last page, optional response fields, trace/correlation, retry 429/503, hard deadline, breaker/recovery, redirect bez tokenu, foreign scopes, malformed/oversized/non-finite/deeply nested body, błędna paginacja, drift i budżety. |
| Prywatny operator CLI i worker | Część 33 testów: pliki 0600, brak overwrite, brak tokenu w output/argv/env i brak odziedziczonego provider secret. |
| Gateway RetailOps | 39/39 passed: wszystkie zasoby, auth/scope przed DB, unknown/authority-changing query, limity i okresy, private policy, DB outage, schema drift i unsupported snapshot. |
| Powiązana regresja legacy | Razem z gateway 45 passed, 2 integration DB skipped lokalnie z powodu wyłączonego Dockera. CI musi wykonać obowiązkowe rzeczywiste DB testy. |
| Typy i jakość AI | Ruff lint/format passed; mypy 333 modułów; wszystkie kontrakty i docs guard passed; wheel/sdist zbudowane. |
| Typy i jakość RetailOps | Pełny runtime lint/format passed; mypy 15 modułów passed. |
| Zgodność ML | Główne uv.lock/pyproject.toml i kod forecast/model lifecycle bez zmian względem 8406ff2. Guard oryginalnego lock SHA passed. |

Źródło ma osobne service credentials i granty; nie używa demo tożsamości.
List/count jednej strony są read-only/repeatable-read z timeoutem. Wersjonowany
digest oraz wygenerowane modele/query są wykonywalnymi bramkami CI.
Transport i testy używają ograniczonych własnych procesów/portów. Głównych
checkoutów, worktree AI 07/08 i ich runtime nie zmieniono.

Source cross-repo drill czyta rzeczywistym przypiętym klientem pięć zasobów
przez HTTP/API/PostgreSQL, 125 sales rows i granty. Wymaga prywatnego fixture
policy oraz jednorazowej bazy CI lub własnego `retailops_ai10_rest_*`.
Tworzy jeden własny UUID produktu i jego rekordy, usuwa tylko ten namespace.
Lokalny Docker jest wyłączony; nie został uruchomiony. Końcowy zdalny CI i
ten rzeczywisty DB odbiór są jeszcze do potwierdzenia na dokładnych commitach.

Fixture dowodzi mechaniki. Nie ma pełnego sales grain, mappingu, immutable
REST snapshot/history/checkpoint handoff lub korekt. `current` nie wynika
z HTTP; starszy timestamp daje `stale`, świeży bez watermarku `unknown`.
Nie ma odbioru modeli AI 07/08, UI, active approved-head ani pełnego E2E
trzech modeli. Nie zastępuj tych dowodów legacy forecast/risk lub licznikami.
