# AI 03.3 — odbiór kontraktu handoff

**2026-09-28, lokalnie.** Branch `ai/03-03-handoff`, osobny worktree na bazie
`1c3b65e2bff68a920c45a5fbfd39b64c2bc80d95` (`origin/main` z początku pracy).
Upstream RetailOps: implementacja fixture/packaging `e26faa2` na bazie `ea63330`.
Wersja handoff **1.0.0**, snapshot 1.0.0/source 2.6.0.
[Runbook](../source-snapshot-handoff.md) opisuje zakres i dokładne granice.
[Rejestr](03-03-handoff.json) podaje file hashes, testy i ograniczenia.

**642 testy passed** całego repo, w tym 15 nowych testów handoff. Ruff check/format,
mypy (93 pliki), schemas/registry checks, docs/CI contract checker oraz budowa
wheel przeszły. Pełne regresje użyły dostępu do rzeczywistych lokalnych portów
HTTP dla istniejących testów. Nowy handoff checker nie używa HTTP, DB ani sieci.

Pełny pakiet ma **76 plików, 2048665 B**. Wewnątrz: 74 pliki snapshotu,
25 tabel Parquet, 31171 wierszy oraz reviewowany contract/expected manifest.
Wszystkie 76 plików są identyczne z upstream; nie kopiowano generatora ani
kodów source validators. Snapshot provenance pozostaje na exporterze `6561481`.

Source: `source-sha256-a12866e1099c3ae2ae7c73cac5c533a35733618d85a3728cd5f0e1c7b527fc00`.
Snapshot: `snapshot-sha256-4d856185ebbe3a8f07dc468468c54eacf69aa40b7d49bfff7e39af8ddde815a4`.

Niezależny checker ma pozytywny odbiór oraz powtórzenie bez zmiany wejściowych
bajtów. Test odłącza registry, fixture i checker od obu repo, uruchamia `python -I`
dwukrotnie i otrzymuje ten sam wynik. Schemat ma wyłącznie lokalne `$ref`.
Corruption, missing/extra/symlink, zmieniony expected/contract, unsupported major,
fake ID, traversal i nieprawidłowa klasyfikacja są odrzucane. `make check`
obejmuje `handoff-check`; Required CI wykona te same kontrole po publikacji.

To lokalny odbiór transportu/schema/identity znanego fixture. Nie implementuje
typed importu dowolnego snapshotu, recomputed typed canonical hashes ani
publikacji do generated. Upstream przelicza typed parity wszystkich 25 tabel;
consumer zrobi to ponownie w **03.4**. Curated to 03.5, full gate to 03.6.
Forecast-source ready nie kwalifikuje modeli ani inventory; 04/06 nadal czekają.
Branch RAG pozostał niezależny. Push/merge i zdalne Required CI tego zakresu
nie zostały wykonane.
