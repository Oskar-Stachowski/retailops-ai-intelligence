# AI 04.1 — lokalny odbiór zadania i kalendarza

Data: 2026-09-29. Repo: `retailops-ai-intelligence`.
Branch: `ai/04-01-task-calendar`, z `origin/main` na
`9eb873a0ae6ec75e444d20aad3bebb7f8820beba`.
Zakres odpowiada wyłącznie punktowi 04.1
[planu](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/etapy/04-forecasting.md).
[Runbook](../forecasting.md) opisuje kontrakt, polecenia i dalszą pracę.

## Wynik funkcjonalny

Wersjonowany task i schemas definiują observed sales, selling grain,
UTC, cutoff `23:59:59`, dostępność bez zaokrąglania mikrosekund,
zerowe dodatkowe oczekiwanie na ingest, fixed-origin horizons 1–14 oraz
okna 7/14. Przyszłe observations i późniejsze corrections nie rozszerzają
granicy wiedzy istniejącego origin. Znany przyszły plan zachowuje ten sam cutoff.
Inventory/truth features oraz operacyjne outputs jako targety są wyłączone.

Manifest powstaje dopiero po pełnej weryfikacji curated i `forecast_source=passed`.
Pin obejmuje source/snapshot/curated IDs i hash deskryptora curated.
Task/calendar identity wiąże resolved config, kod, rodziców, zakres i treść.
Wygenerowany czas i ścieżki nie zmieniają ID. Atomowa publikacja nie nadpisuje
istniejącego manifestu; concurrent/repeated build zachowuje oryginalne bajty.

[Receipt](04-01-calendar.json) pochodzi z uruchomienia
`scripts/check_forecast_calendar.py` na zaakceptowanym fixture `ai-smoke`:

- source → niezależny import → curated → calendar → rebuild/verify;
- 2 originy: 2026-07-16 i 2026-07-17; 28 dziennych targetów;
- 742 oraz 799 historycznych obserwacji, bez danych po cutoff lub po dniu D;
- po 35 znanych planów cen dla horyzontów 1, 7 i 14, wszystkie z jednym cutoffem;
- identyczne ID i bajty publikacji po powtórzeniu, niezmienione źródło i curated;
- 50,128 s łącznie z importem/curated, bez instalacji; nie jest to benchmark modelu;
- 0 wywołań AWS; `forecast_model_status=not_ready`.

## Walidacja

Testy obejmują leap day, granice miesiąca/roku, europejskie daty zmiany czasu
przy zachowaniu UTC, wire ForecastKey i brak podwójnych kluczy w oknach 7/14.
Kontrolowany timeline sprawdza dostępność dokładnie na cutoff, mikrosekundę
po nim i następnego dnia. Nowy origin może poznać nowe fakty, stary pozostaje
zamrożony. Integracja używa rzeczywistego readera curated i znanych planów.

Negatywne przypadki blokują timezone/precision mismatch, nieprawidłowe
horyzonty i okna, przesuwanie historii do target date, truth/final observations,
błędny parent, zmienione hashe/treść/pokrycie originów, duplikaty JSON keys,
symlink input, nieznane pola i nadmierny zakres dat. CLI nie wypisuje wejścia
ani ścieżki danych w odpowiedzi błędu.

**794 testy przeszły w 562,89 s**, w tym 47 przypadków nowego zakresu.
Ruff/format, strict mypy (123 pliki), schemas oraz skany katalogu i historii
Gitleaks przeszły.
Handoff, dwukrotny import/reimport, dwukrotny curated/rebuild/as-of,
wheel/sdist i Compose config przeszły. [Odłączony wheel](04-01-wheel.json)
uruchomiony poza checkoutem zawiera task/schemas i odtwarza ten sam task ID,
calendar ID oraz 742 obserwacje historyczne na skopiowanym source fixture.
Wszystkie bramki `make check` i `make secrets` zostały wykonane lokalnie;
pełna regresja działała równolegle z pozostałymi kontrolami.
`make check` zawiera nową obowiązkową bramkę `forecast-calendar-check`
i kontrolę forecast contract snapshots. Docker build kopiuje nowe schemas
potrzebne przy budowie pakietu.

## Granice odbioru

Brak jeszcze panelu/feature matrix, splitów, baseline, treningu, backtestingu,
kwalifikacji jakości i release’u modelu. Nie nadajemy scoring eligibility ani
label maturity na podstawie samych dat kalendarza. Source calendar świąt/sezonów
pozostaje wejściem do cech 04.2. Odmienne strefy i cutoffy wymagają nowej wersji.
Nie uruchamiano nowej kwalifikacji generatora ani cloud calls.
Nie ma jeszcze push, PR, zdalnego Required CI ani merge tego zakresu na main.

Kolejny zakres: **04.2 — aktywny panel i cechy względem origin, bez inventory**.
