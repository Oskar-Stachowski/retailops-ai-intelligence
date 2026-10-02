# AI 05.7d — odbiór watermarku i świeżości

Data: **2026-09-30**. [Runbook i progi](../forecast-freshness.md),
[raport HTTP/PostgreSQL](05-07-freshness.json),
[przygotowanie rzeczywistego archiwum](05-07-watermark-preparation.json).
Zakres jest odebrany lokalnie. Nie kwalifikuje modelu AI 04 ani nowego
procesu źródłowego inventory; AI 05 pozostaje otwarte.

## Przypięty dowód

Wejście i output **1.1** zachowują deklarację `daily_demand_observations`
z dokładnego descriptora curated oraz osobną dostępność obserwacji w cutoff.
Content identity obejmuje dowód. Walidator sprawdza hash/ID rodzica,
deklarację i obserwacje przeciw typed history; scope wykonania zawęża wykaz
obserwacji. SQL i reader porównują metadata z niezmiennym profilem rejestracji.
Starszy format 1.0 zachowuje canonical IDs, bez dopisywania watermarku.

Polityka `forecast-read-v2`: origin do **86400 s**, watermark obejmujący
origin, obserwacja z lagiem do **1 dnia** ze względu na dostępność dobowego
close. Przekroczenie 24 h albo nowsza nieopublikowana próba daje `stale`.
Brak/niegotowa/nieobsługiwana deklaracja daje `unknown`. Czas publikacji
lub ponownego scoringu nie zastępuje tych warunków. Clock freshness nie
zmienia prediction IDs ani view hash.

## Rzeczywiste przygotowanie wejścia

Bez treningu przygotowano pakiet 1.1 ze zweryfikowanych archiwalnych
features/curated AI 04. Nowy profile ID:
`batch-profile-sha256-54a0b5eef185074726fc169e4d6d1c013f81dd5bdfde8b74668fd95af4445ad8`.
Obejmuje 14 horyzontów jednego produktu/lokalizacji w kanale online,
origin **2026-07-12 23:59:59 UTC**. Rodzic features i jego lock pozostały
identyczne; starego archiwum nie zmieniano.

Deklaracja źródła ma complete through **2026-07-31** i as-of
**2026-08-01 00:00:00 UTC**. Obserwacja rzeczywiście dostępna w historycznym
cutoff kończy się **2026-07-11**. Wykaz nie sięga do późniejszych actuals,
a efektywny watermark jest ograniczany przez origin. Dzisiejszy replay tego
pakietu byłby `stale` ze względu na stary origin. Nie rejestrowano modelu,
nie dokonywano promocji i nie publikowano rzeczywistych prognoz.

## HTTP, PostgreSQL i recovery

Polecenie: `python scripts/check_forecast_read.py --report reports/ai05-freshness-acceptance.json`.
Kontroler utworzył własny projekt **`retailops_ai_read_7a77454d6f`** bez portów
hosta, zainstalował pakiet w obrazie
`sha256:9afc88049775244c0b5d77fb4521853ddf9acd03cdd1876e80d48b29701ab3a4`
i zastosował migrację **0014_forecast_freshness**. Trwałego stosu nie migrowano.

W bazie były **42 syntetyczne manifesty** (35 legacy + 7 watermark),
43 partycje, 44 runy/próby i 8 profili. Trzy release'y i ich gates to
SQL-only stuby, bez rzeczywistego MLflow lub model binary.

Przeszły rzeczywiste żądania HTTP:

- `current` dla kompletnych danych i osobno dla obserwacji poprzedniego dnia;
- `stale` dla opóźnionego watermarku i obserwacji starszej o 2 dni;
- `unknown` dla `not_ready`, nieobsługiwanej polityki i braku deklaracji;
- scope przed count/budget, 401/403/422, niewidoczny pin 404 i stable strony;
- legacy replay i nowsza nieudana próba, bez wycofania kompletnego wyniku;
- brak descriptora, URI i nieautoryzowanych produktów w publicznym wyniku.

Przyszła deklaracja została odrzucona bez zmiany stanu. Kontrolowany rehash
zmienionego dowodu w SQL INSERT dostał `forecast_output_freshness_input_binding`;
cała publikacja została wycofana, a run pozostał do jawnego zamknięcia.
Table-owner injection w jednorazowej bazie dała bezpieczne 503 przy odczycie
uszkodzonego watermarku. `finally` przywrócił dokładny manifest; HTTP ponownie
zwróciło poprawny `current`. Normalne zapisy nie wyłączają triggers.

SIGKILL/restart zachował pełny stan wszystkich tabel, view i predykcji.
Hash stanu: `794359c79bc8e5f96fdaa112a92a16e2f318ca18d167f5e37f7f1926b140a9c9`.
Kontroler usunął własne kontenery, wolumeny i tag. Rozszerzony smoke ma
budżet kontrolera **420 s**; nie zmienia to limitów runtime ani SQL 3 s.

## Kontrole i bieżące bramki

Przeszło **365 różnych testów**: 60 read/publication/runtime,
159 access/HTTP/CI/persistence/cleanup oraz 146 freshness/queue/inputs/catalog/
evaluations/lifecycle/store (20 z tej grupy sprawdza nowe przypadki freshness).
Końcowy snapshot access i 18 testów legacy read ponowiono po zmianie polityki.
Ruff/format, mypy (230 plików), wszystkie snapshots kontraktów,
gitleaks oraz budowa wheel/sdist przeszły. Wersjonowany fixture 1.1
`contracts/forecast_jobs/v1/fixture/watermark-inputs.json` jest jawnie syntetyczny.

Read smoke należy do istniejącego persistence Required CI; nie ma jeszcze
zdalnego odbioru tego brancha. Pozostają spójny **qualified handoff AI 04**,
pełny batch na jego release'ie oraz zdalny Required CI. Źródło inventory
bez zadeklarowanego dziennego watermarku pozostaje `unknown`; jego nowy
proces wymaga własnej kwalifikacji modelu. Outbox i zdarzenia należą do AI 10.
