# Odbiór pierwszego zakresu AI 12

**2026-09-29 · lokalny katalog narzędzi i wykonawca.** Zakres jest na osobnym
branchu `ai/12-tools`, opartym o zamknięty AI 11 (`abf3f69`).
[Instrukcja](../agent-tools.md), [status](../STATUS.md).
[Zapis kontroli i checksum](12-tools.json).

Osiem narzędzi ma Pydantic input/output, wersjonowane JSON Schemas i jawne
capabilities. Forecast zachowuje istniejący kontrakt i lineage; pozostałe dane
mają typed fixtures. Pinned knowledge adapter stosuje backend AI 11.
To odbiór interfejsu agenta, nie ponowny pomiar jakości Titan ani rzeczywisty
odbiór przyszłych źródeł sprzedaży/inventory/ML.

## Kontrole

Pełna regresja: **751 passed**, 132,87 s. Po dodaniu ochrony przed mutacją
scope wykonano końcowy właściwy zestaw **109 passed**, 1,82 s.
Ruff/format (170 plików), strict Mypy (106 plików), docs links/CI contract,
intelligence/access/knowledge/agent snapshots, wheel/sdist, Compose config
oraz Gitleaks przechodzą. Nowe capability rozszerzają istniejące access schemas;
stare granty i przykłady zachowują dotychczasową semantykę.

Nowe testy obejmują cały katalog i następujące granice:

- Tożsamość z server-side LocalAccess, jawne assistant/read capabilities,
  odrzucenie admin/viewer bez nadania praw i nieautoryzowanego pełnego scope.
- Dodatkowe pola identity/role/URL/path/SQL/shell, nieznane i zapisujące
  narzędzia nie docierają do adaptera.
- Daty, okres, horizon, limity, serwerowy default i jawny opt-in fixture.
- Rewalidacja wyników: inny produkt/lokalizacja/kanał/okres, duplikaty,
  nadmiar wierszy, przyszłe/stale dane, podmiana provenance i niewalidowalny ref.
- Brak adaptera/case jest `unavailable`; sprawdzone puste źródło jest `no_data`.
- Wspólny limit 6 wywołań przy 10 równoległych próbach; deadline nie resetuje
  się, timeout/anulowanie zużywa próbę, retry nie jest wykonywany automatycznie.
- Pinned RAG przekazuje serwerowy principal/pin; brak pinu, niedozwolony
  scope oraz zmiana index ID są odrzucane. W testach użyto syntetycznych wektorów
  i backend spy, bez AWS i bez nowej bazy.
- Wyjątki są kanoniczne i nie kopiują szczegółów adaptera do error/audit.
  Scope przekazany adapterowi jest oddzielną kopią, żeby mutacja nie rozszerzyła
  zakresu używanego do kontroli odpowiedzi.

## Granice i kolejny zakres

Ten odbiór dotyczy pierwszego zakresu narzędzi. Aktualne bramki AI 12 podaje
[status](../STATUS.md); konfigurację/prompty i fake chat opisuje
[kolejny odbiór](12-chat.md).

Nie wykonano wywołań AWS, wdrożenia chmurowego ani zapisów operacyjnych.
Pełne zamknięcie AI 12 wymaga odebranego AI 10 i AI 11. Równoległe AI 03
pozostaje oddzielnym strumieniem z własnym worktree.
