# Odbiór tożsamości i uprawnień etapu 01

Pomiar **2026-09-28**, macOS ARM64, Python 3.11.15, uv 0.12.19.
Implementacja `01eca67b1af4d0e082770ddb3e26dee601fa9d02`;
baza `40ad85c39551f00546b994e902e476f0b171ac07`.
[Raport](01-access.json), [uruchomienie i semantyka](../access-control.md).

## Wykonane kontrole

`make ci-local` — exit 0: **267 passed in 9.01s**, bez pominięć.
Ruff/format, Mypy strict (48 plików), docs, intelligence i access snapshots,
wheel/sdist, Compose config i oba skany Gitleaks przechodzą.
Lockfile i runtime dependencies nie zmieniły się; brak zależności JWT/IdP/AWS.
Diagnostyczny OpenAPI i schemas HTTP oraz intelligence fixtures pozostały zgodne.

Testy używają rzeczywistego loadera i prywatnych plików:
brak/malformed policy, permissions/owner/symlink/FIFO/rozmiar/duplicate JSON;
osobne losowe credentials, fingerprint/principal refs, expiry/not-before/revoked.
Polityka jest snapshotem; test odróżnia odwołanie po restart/load od expiry
sprawdzanego bez restartu.

ASGI sprawdza 401/403, niehierarchiczne capabilities, scope product/location/channel,
odmowę całego mieszanego zakresu, role/body/query/header/cookie spoofing, osobne
metryki, rozmiar/deadline requestu, bezpieczne błędy i logi/etykiety.
Grant schemas/examples i wszystkie trzy chronione operacje mają sprawdzony OpenAPI.
Celowe usunięcie security ze snapshotu daje exit 1, bez jego naprawy w CI.

Dodatkowy rzeczywisty proces loopback wykonuje access-init i serve z plikiem polityki:
identity, poprawny forecast preflight, 3 odmowy scope, viewer→admin 403,
admin metadata 200, admin→forecast 403, body role 422 oraz health/readiness.
Lifespan kończy się poprawnie; stdout serwera jest pusty, logi nie mają wygenerowanych
tokenów ani ścieżki polityki. Nie utworzono katalogu artefaktów.

## Czysty checkout i zainstalowany wheel

Czysty checkout implementacji, nowy venv: `make bootstrap ci-local` — exit 0,
**267 passed in 12.27s**, bez pominięć. Wszystkie bramki i skany przechodzą,
worktree pozostaje czysty. Actionlint przechodzi.

Wheel z tego checkoutu zainstalowano w osobnym venv z hash-verified produkcyjnymi
zależnościami z uv.lock. `uv pip check` przechodzi; import z site-packages,
bez jsonschema dev i źródeł na PYTHONPATH. Poza checkoutem uruchomiono initializer
oraz rzeczywisty serwer: identity 401/200, scope 200 i trzy 403, admin 403/200,
admin bez forecast capability 403, podmieniony principal 422 ze stałym błędem.
Polityka została unieważniona na dysku: działający snapshot nadal dopuszczał token,
a nowy proces po restarcie dawał 401. Oba lifespan zakończyły się poprawnie.
Pliki mają 0600, katalog 0700; tokenów i ścieżki polityki nie ma w logach/odpowiedziach.
Health/readiness pozostały 200, katalog artefaktów nie powstał. Checksum wheel w raporcie.

## Ograniczenia

To lokalna granica dostępu, bez OIDC/JWT/AWS/publicznego wdrożenia.
Forecast-check sprawdza uprawnienia do zakresu; nie odczytuje danych lub modeli.
Tool nie ma executora. Nie odebrano produkcyjnego rate limit/tenant/audit store.
Compose nie montuje policy, więc nowe endpointy API są tam zamknięte.
Nie ponawiano pełnego smoke DB/crash/restart; migracje/Compose runtime są bez zmian,
a [wcześniejszy pomiar](01-persistence.md) zachowuje własny zakres i datę.
Nowe commity pozostają lokalne; odbiór zdalnego Required CI czeka na push.
Etap 01 pozostaje in_progress do tego odbioru.

