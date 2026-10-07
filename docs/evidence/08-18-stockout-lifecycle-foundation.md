# AI 08.18 — podstawa lifecycle stockout

Rodzic: `62f28db241b47ef762ccd6d033b13aa3406af4d8`.
[Kontrakt](../reference/stockout-lifecycle.md) dodaje stockout-only typed
approval/binding/release i append-only journal/migrację PostgreSQL.
V12 korzysta z tego samego recoverable engine; osobne schematy i namespace
zachowują granice produktów.

23 nowe testy stockout przechodzi. 79 istniejących testów v12 lifecycle,
publication, queue i API przechodzi (442,16 s). Wspólny run 23 nowych oraz
35 istniejących lifecycle-store/readiness daje 58 passed, 0 ostrzeżeń,
2,08 s. Mypy 415 źródeł i Ruff/format przechodzą. Alembic ma pojedynczy head
`0020_stockout_lifecycle`, 20 rewizji.

Nowy rzeczywisty checker PostgreSQL jest zbierany poprawnie, ale nie został
jeszcze wykonany. Włącza się do istniejącej prywatnej akceptacji AI 05 i
backup/restore w Required CI. Nie jest counted jako zaliczony test integracji.
Rejestr stockout w tym checkerze jest double; wymagany prawdziwy MLflow i
verifier finalnego pakietu pozostają otwarte. Produkcyjna baza nie była migrowana.

[CI producenta późniejszych danych](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37244283406)
jest zaliczone. [Dwie późniejsze kohorty liczą się](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37245910420),
trzecia czeka; wyniki końcowej jakości nie zostały otwarte.

**Nie utworzono prawdziwego approval lub produkcyjnej promocji. Threshold/capacity,
finalna kampania, trwały batch/read, realny MLflow stockout i whole AI 08 ready
pozostają otwarte.**
