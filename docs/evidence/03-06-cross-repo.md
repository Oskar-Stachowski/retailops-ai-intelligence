# AI 03.6 — pełny odbiór cross-repo

Właściciel generacji i końcowej bramki: RetailOps. Właściciel typed importu,
curated i as-of: AI. Nie łączymy tego worktree z trwającym AI 12.

[Bieżące evidence obu repo](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/ai/03-01-parquet/docs/evidence/ai/03/03.6/README.md)
zawiera pełne wyniki, manifesty, piny, bramki i decyzję wejścia do 04/06.
[Runbook cross-repo](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/ai/03-01-parquet/docs/reference/ai03-cross-repo.md)
opisuje odtworzenie bez bazy RetailOps, brokera i AWS.

Bramka generuje standardowe `ai-smoke` i `ai-temporal-smoke` od początku,
dwukrotnie, seed 42 / koniec 2026-07-31. Recomputes qualification, typed CSV/
Parquet parity, immutable export/reexport, import/reimport, curated/rebuild,
pełną weryfikację i niezależny as-of. Każdy pełny profil ma 300 s / 1024 MiB,
bez instalacji zależności. Kontrolowany osobny fixture opóźnionej sprzedaży
sprawdza poprzednią i nową quantity version na mikrosekundowej granicy.

[Required CI 5190134](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36537300250)
przeszedł: **743 testy w 397,96 s**, Ruff, format, strict mypy (117 plików),
kontrakty i docs, fixture handoff, typed import i curated dwukrotnie,
wheel/sdist i Compose config; osobny job zaliczył real Compose/persistence.
Obraz API zawiera oba zestawy kontraktów wymagane do budowy pakietu.

Publikacja: [AI PR #5](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/5)
i [RetailOps PR #65](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/65).
PR-y są oddzielne; publikacja brancha i Required CI nie oznaczają merge na main.

Dane zachowują wszystkie facts/plans oraz quantity versions. Truth pozostaje
w osobnej przestrzeni parent importu, bez automatycznego joinu lub dostępu API.
Forecast source może być ready przy inventory false; forecasting/model,
anomaly, stockout i replay wymagają dalszych własnych bramek.
Po zmianach źródła w 06/07 tworzymy nowe IDs i ponawiamy zależne oceny.
