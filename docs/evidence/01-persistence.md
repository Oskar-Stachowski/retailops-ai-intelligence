# Persistence etapu 01

Pomiar **2026-09-28**, lokalny macOS ARM64, Docker Desktop, Python 3.11.15,
uv 0.12.19; implementacja `7e0978b7a3d14ba222c7c305d8b995e960369fc1`, baza `7d67530`. [Raport maszynowy](01-persistence.json)
zawiera datę, digest obrazów i sumę lockfile. [Uruchomienie](../local-stack.md).

## Rzeczywiste usługi

`.venv/bin/python scripts/verify_local_stack.py` — exit 0.
PostgreSQL 16/pgvector 0.8.6, MLflow 3.16.1 i API zostały rzeczywiście zbudowane
i uruchomione. API działa z zainstalowanego wheel w obrazie, bez editable/source mount.

- Oddzielne bazy i role AI/MLflow; obie próby połączenia do bazy drugiej aplikacji
  zostały odrzucone. Brak superuser/createdb/createrole dla aplikacji.
- Jawne migracje AI i MLflow, ponowienie AI bez błędu. Inna wersja schematu
  dała /ready 503 i /health 200. Restart API nie zmienił tej wersji.
- Zapisano metadane AI oraz eksperyment/run i artefakt przez HTTP MLflow.
  SIGKILL trzech usług i restart zachowały rekord AI i treść artefaktu.
- Zatrzymanie PostgreSQL dało /ready 503 z ai_db down/timeout oraz /health 200.
  Po powrocie DB /ready wróciło do 200 bez restartu API.
- Down/up zachowało metadane AI, eksperyment MLflow oraz identyczną treść pliku.
  Zakończenie testu zatrzymało kontenery, pozostawiając oba wolumeny.
- Rzeczywiście opublikowane porty były 127.0.0.1:8081 i 127.0.0.1:5010.
  DB nie ma portu hosta. Internal backend chroni DB; osobny frontend umożliwia
  publikację portów na Docker Desktop. Nie deklarujemy blokady egress API/MLflow.
- W logach końcowego uruchomienia usług nie znaleziono wygenerowanych haseł ani
  tokenu metryk. Metryki bez tokenu zwróciły 401.

## Kontrole pakietu

77 testów bez pominięć; Ruff/format i Mypy strict (25 plików) przechodzą.
Test zamkniętego portu używa rzeczywistego psycopg. Testy wyboru revision/vector
używają fake adaptera; nie są dowodem runtime PostgreSQL. Rzeczywiste próby wyżej
stanowią osobny odbiór. Wersjonowane schema HTTP odzwierciedlają rolę ai_api.

Linki, kontrakt CI, build wheel/sdist, Compose config i actionlint sprawdzone.
Wymagany job persistence wykonuje pełny smoke, a required-result zależy od niego.
Plik poświadczeń pozostaje ignorowany przez Git i poza kontekstem builda.

## Czysty checkout i GitHub

Czysty checkout implementacji, nowe venv i nowe wolumeny:
`make bootstrap ci-local` — exit 0, **77 passed in 8.80s**, bez pominięć;
lint/format, Mypy, kontrakty, dokumentacja, build, Compose config i oba skany
Gitleaks przeszły. Ponowiony `scripts/verify_local_stack.py` — exit 0,
wszystkie rzeczywiste próby również przeszły. Worktree pozostał czysty.
To potwierdza bootstrap nowej bazy oraz dostępność migracji w zbudowanym wheel.

Repo AI ma origin na GitHub. Odczyt API 28.09.2026 potwierdził chronione main
z wymaganym required-result oraz
[udany Required CI bazowego 7d67530](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36383297184).
Nowy commit persistence nie został wypchnięty, więc jego zdalne CI nie jest odebrane.

## Ograniczenia

Etap 01 pozostaje in_progress: kontrakty danych/run/tool, uprawnienia nowych
endpointów są następną pracą. Po push wymagany jest zdalny CI nowego zakresu;
stan GitHub wyżej dotyczy bazowego commitu.
Nie wykonano Linux x86_64 runtime ani AWS. To lokalny development, bez TLS,
aplikacyjnego auth MLflow, backup/restore, pipeline importu, modeli, RAG lub agenta.
Trwałość po crash/restart nie dowodzi odtwarzania po utracie wolumenu.
