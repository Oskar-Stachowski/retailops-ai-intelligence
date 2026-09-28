# Persistence etapu 01

Pomiar **2026-09-28**, lokalny macOS ARM64, Docker Desktop, Python 3.11.15,
uv 0.12.19; baza kodu `7d67530`. [Raport maszynowy](01-persistence.json)
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

## Ograniczenia

Etap 01 pozostaje in_progress: kontrakty danych/run/tool, uprawnienia nowych
endpointów i zdalne CI/ochrona repo są następną pracą. Repo AI nadal nie ma remote.
Nie wykonano Linux x86_64 runtime ani AWS. To lokalny development, bez TLS,
aplikacyjnego auth MLflow, backup/restore, pipeline importu, modeli, RAG lub agenta.
Trwałość po crash/restart nie dowodzi odtwarzania po utracie wolumenu.
