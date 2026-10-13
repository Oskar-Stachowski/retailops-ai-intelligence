# AI12 — odbiór poprzedniego CI i integracja optymalizacji

2026-10-07, **in_progress**, [draft PR32](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/32).
[Receipt](12-ci-integration.json) uzupełnia historyczne wyniki
[adaptera v12](12-native-forecast.md); nie nadpisuje poprzednich zapisów.

[Required CI 37615009993](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37615009993)
zakończył się **success** dla `3dd53c2`: wszystkie 14 jobów, w tym cztery
shardy, cztery acceptance, PostgreSQL/MLflow persistence, rzeczywisty TensorFlow
CPU training/artifact/reload, anomaly OCI z pełnymi świeżymi rodzicami oraz
końcowy `required-result`. Jest to odbiór tego poprzedniego checkpointu;
nie zastępuje CI nowego adaptera ani workflow.

Checkpoint natywnego adaptera `fd9e892` ma 630/630 wybranych regresji,
pełne `ci-checks`, fake golden 50/50 i 36/36 critical oraz równość 534 plików
Python w wheel/checkout. Aplikacja i oba runtime lockfile'y nie zmieniły się
przy integracji optymalizacji CI.

Przejrzany commit `02554284` z osobnej sesji
[PR34](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/34)
został cherry-picknięty do AI12 jako `8280382`. Zmiany:

- Pełne CI dla PR i `main`; gałąź bez PR używa pełnego `workflow_dispatch`.
  Brak zdublowanego push/PR i brak pozornie zielonego pomijania testów.
- Ciężkie joby zależą od checks/secrets. Cztery shardy mają JUnit i receipts
  faz setup/call/teardown, sprawdzane przez końcowy wymagany job.
- `full-raw-dq` i `day-qualification` przeniesiono między acceptance bez
  usuwania bramek. Forecast persistence ma osobny wymagany job.
- Budowa obrazu anomaly nakłada się na świeżą generację danych; obrazy są
  wymagane przed acceptance. Dodano bezpieczne pomiary faz. Nie używa się
  historycznych danych zamiast świeżych publicznych rodziców.

Zachowano `agent-evaluate` w `ci-checks` i guardzie repozytorium, natywne testy
AI12 oraz jawny frozen training lock w anomaly. Po integracji: 191/191 testów
CI/lock/native/HTTP i pełne `ci-checks` passed, mypy 653 pliki, fake golden
50/50 oraz 36/36 critical. Gitleaks drzewa passed.

Plan pełnej kolekcji obejmuje **3992 testy / 182 pliki** w czterech rozłącznych
shardach; wszystkie 41 nowych przypadków native forecast są obecne dokładnie
raz. Plan nie jest wynikiem wykonania wszystkich testów. Estymowane obciążenia
shardów wynoszą 1258,5–1258,6 s na podstawie wag wcześniejszego Linux CI;
rzeczywisty czas i pełne execution receipts wymagają nowego zdalnego runu.

Nowy head PR32 wymaga całego Required CI. Optymalizacja z PR34 nie jest tutaj
oznaczona jako zdalnie zaakceptowana. Pozostałe warunki READY AI12, w tym
rzeczywiste źródła/model chat, fizyczny mapping i przepływ AI10 outbox/v2,
pozostają otwarte. Nie wykonano AWS ani zmian w sąsiednich worktree, procesach,
Compose lub bazach.
