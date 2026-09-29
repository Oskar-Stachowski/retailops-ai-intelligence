# AI 03.6 — pełny odbiór cross-repo

Właściciel generacji i końcowej bramki: RetailOps. Właściciel typed importu,
curated i as-of: AI. Nie łączymy tego worktree z trwającym AI 12.

[Bieżące evidence obu repo](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/ai/03-01-parquet/docs/evidence/ai/03/03.6/README.md)
zawiera pełne wyniki, manifesty, piny, bramki i decyzję wejścia do 04/06.
[Runbook cross-repo](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/ai/03-01-parquet/docs/reference/ai03-cross-repo.md)
opisuje odtworzenie bez bazy RetailOps, brokera i AWS.

Bramka generuje standardowe `ai-smoke` i `ai-temporal-smoke` od początku,
dwukrotnie, seed 42 / koniec 2026-07-31. Przelicza kwalifikację, sprawdza typed
CSV/Parquet parity, niezmienny eksport, import, curated, pełną weryfikację
i niezależny as-of. Każdy pojedynczy pipeline ma 300 s / 1024 MiB,
bez instalacji zależności. Dodatkowy re-export upstream i reimport/rebuild
downstream są odrębnymi obowiązkowymi kontrolami idempotencji w Required CI.
`check_curated.py` domyślnie wykonuje także te dodatkowe kontrole;
`--pipeline-only` służy wyłącznie workerowi pełnej bramki cross-repo.
Kontrolowany osobny fixture opóźnionej sprzedaży
sprawdza poprzednią i nową quantity version na mikrosekundowej granicy.

Importer wylicza zakresy dat na podstawie jawnego schematu Arrow także dla
pustych tabel i kolumn zawierających wyłącznie null. Zachowuje wtedy
`date_start=null`, `date_end=null`, `value_count=0`; nie ufa zadeklarowanym
licznikom. Cztery przypadki regresji obejmują poprawne i zmienione zakresy.
Poprawka importera: `cb053cc29c1906d79108cba2c70f88b56e454211`.
Zmiana wykonywanego kodu zmienia fingerprint i curated ID, zachowując source
oraz snapshot ID dla niezmienionych danych.

[Required CI 5190134](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36537300250)
przeszedł: **743 testy w 397,96 s**, Ruff, format, strict mypy (117 plików),
kontrakty i docs, fixture handoff, typed import i curated dwukrotnie,
wheel/sdist i Compose config; osobny job zaliczył real Compose/persistence.
Obraz API zawiera oba zestawy kontraktów wymagane do budowy pakietu.
[Required CI b2c19e8](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36538987812)
potwierdza również pełne kontrole po rozdzieleniu pomiaru pojedynczego pipeline
od dodatkowych prób idempotencji. Wynik dla bieżącej rewizji wraz z regresją
importera jest przypięty w końcowym evidence RetailOps.

Publikacja: [AI PR #5](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/5)
i [RetailOps PR #65](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/65).
PR-y są oddzielne; publikacja brancha i Required CI nie oznaczają merge na main.

Dane zachowują wszystkie facts/plans oraz quantity versions. Truth pozostaje
w osobnej przestrzeni parent importu, bez automatycznego joinu lub dostępu API.
Forecast source może być ready przy inventory false; forecasting/model,
anomaly, stockout i replay wymagają dalszych własnych bramek.
Po zmianach źródła w 06/07 tworzymy nowe IDs i ponawiamy zależne oceny.
