# AI 08.19 — trwała kolejka i API fizycznego ryzyka

[Kontrakt](../reference/stockout-jobs.md) opisuje przyrost. 199 testów przechodzi:
163 dotyczą zmienionych granic, a 36 to regresja HTTP. Ruff/format, Mypy,
generowane schematy, dokumentacja i kolekcja obu checkerów PostgreSQL są zaliczone.

Readiness wymaga nowego head 0021. Rzeczywisty SQL i pełny backup/restore tego
przyrostu oczekują. Bazy innych sesji pozostają nietknięte. Poprzedni SQL 0020
i backup przeszły w [persistence CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37246436824/job/111565234443).

Późniejsze światy 42/137 mają 2627/2609 punktów TEST membership; metryk modeli
na nich nie otwarto. Przygotowanie trwało 1966,35/2085,03 s, peak RSS
1067,40/1134,53 MiB, allocated scratch 490,78/506,26 MiB, w limitach.
ZIP i publiczne receipts zweryfikowano; prywatnych rodziców nie odtworzono
lokalnie. Seed 2026 i odbiór wszystkich źródeł pozostają otwarte.

**To nie jest finalna jakość, zgoda na progi, odbiór produkcyjny ani AI 08 ready.**
