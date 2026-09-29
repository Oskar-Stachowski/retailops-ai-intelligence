# AI 06.6b.2c.1 — lokalny odbiór importu inventory

29.09.2026, Darwin/ARM64. [Wyniki](06-inventory-handoff.json) wiążą importer
`891a7615466e467e1a37454a0f2af5a464605bbd` na branchu `ai/06-inventory-handoff`
z exporterem RetailOps `6b40f11086dc810d31a79bf6c0e6483a5834ed23`.
[Runbook](../reference/inventory-snapshot-11.md) podaje CLI, limity i zakres kontroli.

Oba standardowe profile mają świeże powtórzenia source → qualification →
snapshot → import/reimport/verify. Wszystkie trzy IDs i snapshot descriptors
są identyczne między powtórzeniami; opublikowane input/output nie są zmieniane.
43 facts/plans zachowują typy i source hashes. 12 private tables oraz
qualification wymagają explicit opt-in. Semantic forgeries po przeliczeniu
hashów nadal failują na niezależnym ledger/snapshot/routing/finance/receipt check.

| Przebieg | Czas całego zakresu s | Max RSS obu procesów MiB |
|---|---:|---:|
| ai-smoke first | 66,82 | 215,36 |
| ai-smoke repeat | 66,43 | 214,48 |
| ai-temporal-smoke first | 178,45 | 286,75 |
| ai-temporal-smoke repeat | 163,32 | 311,61 |

727 testów RetailOps i 773 testy AI przeszły, bez failures/skips. Ruff i mypy
przeszły. Wheel z packaged contracts odczytuje facts/private bez producer imports
i registry checkout fallback. 80 wcześniejszych fixture/contract files w AI
pozostaje byte-identycznych z bazą; snapshot 1.0 i curated 1.0 mają nadal odbiór.

Odbiór jest lokalny; nie wykonano nowego push/remote Linux CI. Kod pozostaje
na osobnym branchu/worktree. SOURCE/global inventory/model readiness pozostają false.
Curated 1.1, pełny pipeline wraz z curated, domyślne przełączenie source i
ponowna ewaluacja 04/05 są kolejnymi bramkami. Curated 1.0 odrzuca snapshot 1.1
jawnym kodem `inventory_curated_contract_not_yet_supported`.
