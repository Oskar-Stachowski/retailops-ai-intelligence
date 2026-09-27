# Weryfikacja bazowego HTTP w AI 01

2026-09-27, macOS ARM64, Python 3.11.15, uv 0.12.19.
Baza repo AI: `5da5fdc04e8e44510a489c0f0cc45557cf87e15d`.
Plan RetailOps: `c89da89`. Zakres: diagnostyka HTTP roli foundation,
kontrole zależności z provider fakes, błędy, telemetry i rzeczywisty proces lokalny.

Kontrakt i komendy: [HTTP](../http-service.md), [development](../development.md).
Nie jest to odbiór DB/MLflow, trwałości danych, predykcji, AWS ani zdalnego CI.


## Wykonany przebieg

`make ci-local`: **62 passed in 2.23s**, bez pominięć i ostrzeżeń; Ruff/format,
Mypy strict (18 plików), kontrakty HTTP i CLI, linki, wheel/sdist, Gitleaks
historii i katalogu. `actionlint -shellcheck='' .github/workflows/required-ci.yml`
oraz `git diff --check`: exit 0.

Testy obejmują 503 przy wymaganej awarii/wyjątku/timeout, 200 degraded dla
opcjonalnego providera, powrót do gotowości, niezależność health, timeout kolejki,
brak sekretów w 400/401/403/404/405/422/500/503, autoryzację metryk, ograniczenie
etykiet, W3C parent/child i przekazanie nagłówków do fałszywego providera,
równoczesne żądania oraz wyłączony SDK tracingu. Kontrakt 503 sprawdza safe
provider exception; kod wyjątku ani jego treść nie opuszcza serwisu.

Test procesu uruchomił `python -m retailops_ai serve` na krótkotrwałym porcie
loopback, sprawdził cztery endpointy, 401/200 metryk, bezpieczne JSON logs
i zakończenie lifespan po SIGTERM. Uvicorn 0.54 zachowuje sygnał po łagodnym
shutdown; test wymaga zdarzenia `application_stopped` oraz kodu `-SIGTERM`.
Pierwsza próba w sandboxie została zablokowana na bind; właściwy test wykonano
z uprawnieniem do lokalnego socketu, bez pomijania kontroli.

Zależności są przypięte w `uv.lock`. Zainstalowany Starlette wskazał httpx2
jako aktualnego klienta TestClient; testy używają httpx2 2.13.1.
[Zapis maszynowy](01-http.json) zawiera datę UTC i hashe faktycznie sprawdzonego
kodu/kontraktów/lockfile. Wynik lokalny nie jest wynikiem GitHub Actions ani
odbiorem usługi zależnej od rzeczywistej DB.
