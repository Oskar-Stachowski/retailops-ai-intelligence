# Odbiór lokalny AI 05.5b — trwałe wejścia i supervisor

Data: **2026-09-30**. Branch `ai/05-mlflow-serving`.
[Runbook](../forecast-input-store.md), [rzeczywisty odbiór rejestru](05-05-input-store.json)
i [regresja kolejki po migracji](05-05-queue-regression.json).
Odbiory używają świeżych projektów Compose bez portów hosta; po próbach
usuwają własne wolumeny. Stosów innych sesji i worktree AI 04 nie zmieniano.

## Rzeczywisty rejestr PostgreSQL

Projekt `retailops_ai_inputs_497ac67f8d` użył pakietu przygotowanego i
zweryfikowanego w 05.5a: origin 2026-07-12, jeden produkt/lokalizacja,
online, 14 wierszy i jedna historia. Pełne JSONB zajęło **234 313 B**.
Profil zachował ID
`batch-profile-sha256-4ca3b2009a1130f0b64f95dcfc0ae14d87129463eca59b7f9ce429b36e63aa8c`.

- Dwa równoczesne zapisy zwróciły dokładnie jeden profil i taki sam receipt:
  pełną treść, hash oraz timestamp bazy. Ponowny odczyt był identyczny.
- `local` nie mógł czytać wpisu `test`; niezależna rejestracja zachowała
  ten sam content ID w drugim namespace.
- Limity liczby profili i rzeczywistego rozmiaru JSONB odmówiły zapisu
  7-dniowego wariantu, bez częściowego wpisu. Sprawdzono również rollback
  po insert, gdy JSONB był większy od canonical JSON.
- Baza odrzuciła update treści, update timestampu i delete profili.
- Zainstalowany worker pobrał zarejestrowany profil i odrzucił nieistniejący
  zatwierdzony release statycznym błędem; nie utworzył runu ani outputu.
- SIGKILL/restart bazy zachował identyczny hash pełnej treści i receipts
  wszystkich trzech wpisów: dwa profile `test`, jeden `local`.

Raport wskazuje digest użytego obrazu i SHA pełnego stanu.
Rejestracja oznacza `verified_inputs_only`, bez kwalifikacji modelu.
**0 model releases, 0 forecast runs i 0 opublikowanych prognoz.**

## Supervisor i regresja

Testy używają rzeczywistych podprocesów do sprawdzenia odcięcia poświadczeń,
bindingu profile/release/count, limitu stdout/stderr, wall time oraz RSS.
Przekroczenie RSS i odmowa callbacku lease kończą własny child przez SIGKILL
i reaping. Kontrola rzeczywistego executora odrzuca inny zainstalowany lock
przed dostępem do Registry. Unit mock qualification nigdy nie jest
rejestrowana w rzeczywistym Registry.

Regresja kolejki na nowej migracji powtarza HTTP/PG/MLflow, fenced retry,
SIGKILL po częściowym obliczeniu, zmiany aliasu, deadline i backpressure.
Wyniki pozostają jawnie mechanics fixtures; nie zatwierdzają jakości AI 04.
Historyczny raport 05.4a zachowano, a nowy pomiar zapisano osobno.

[Odbiór ścieżki CI](05-05-input-fixture-store.json) używa kontrolowanego,
kompletnego typed fixture, a nie danych potrzebnych do kwalifikacji AI 04.
Projekt `retailops_ai_inputs_7be9e22a89` potwierdził także start izolowanego
Linux executora pod kernel CPU/file/address-space limits oraz odmowę
niepoprawnego wejścia przed dostępem do Registry. Nowa bramka
`forecast-input-store-smoke` należy do Required CI i ma test usunięcia bramki.

**17 testów nowego modułu** przechodzi (69,34 s), **24 testy loadera**
przechodzą (105,32 s) i **142 testy regresji** przechodzą (2,96 s).
Zakresy nie nakładają się. Regresja obejmuje kolejkę, HTTP, persistence,
model lifecycle, Registry, import MLflow, combined store i CI guards.
Ruff/format (240 plików), mypy (200 modułów), snapshoty kontraktów,
linki dokumentacji i bramki CI przechodzą. Wheel/sdist buduje się;
oba odbiory PostgreSQL korzystały ze zbudowanego, zainstalowanego pakietu.
Gitleaks sprawdził pliki i historię Git bez wycieków.

## Granice i następny zakres

Nie odebrano wykonania rzeczywistego zakwalifikowanego modelu: na tym branchu
nie ma takiego release’u, a stare archiwum ma `not_ready` i niezgodny lock
features. Nie zmieniano jakości, progów, pinów archiwum ani budżetu AWS.
Prepared inputs są w osobnej trwałej tabeli; publiczna kolejka nadal nie
przyjmuje ich jako qualified forecast runs. Supervisor i hook lease są
gotowe do integracji z atomową publikacją AI 05.6; preflight nie zmienia
stanu kolejki. AI 05 pozostaje otwarte.
Pełne `make ci-local` i zdalny Required CI pozostają bramką przed PR.
