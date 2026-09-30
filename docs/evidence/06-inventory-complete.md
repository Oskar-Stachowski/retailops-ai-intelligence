# AI 06 — kompletny handoff inventory

2026-09-29. [Receipt](06-inventory-complete.json) wiąże runtime AI `2574c06794b7b4f86393f23db1d9e97c16413706`
z RetailOps `f07ba22555a48b6c750f865f609e0893dfc129e0`. [Runbook](../reference/inventory-snapshot-11.md)
opisuje snapshot/import/curated 1.1 oraz historyczny as-of.
Pełna ścieżka obu standardowych profili dwukrotnie mieści się w 300 s / 1024 MiB,
zachowuje IDs i izoluje truth. 784 testów AI przeszło; wheel działa
poza checkoutem na packaged contracts. Historyczne kontrakty/fixtures pozostają niezmienione.
Curated ma inventory ready; modele 04/05/08 wymagają własnego odbioru.

[Pełny audyt i karta danych RetailOps](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/tree/main/docs/evidence/ai/06/final)
opisują źródło, kwalifikację, pomiary i ograniczenia. Zdalny stan publikacji pokazuje
[Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/workflows/required-ci.yml).
