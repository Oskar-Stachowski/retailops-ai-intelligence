# AI12 — zgodność kandydata z drzewem merge CI

2026-10-07, **in_progress**. [Receipt](12-main-binding.json) uzupełnia wcześniejsze
[native forecast](12-native-forecast.md) i [CI integration](12-ci-integration.md).

[Preflight 37623445150](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37623445150)
dla head `1fda7fc` odmówił wykonania agent-evaluate. Manifest był związany z kodem
gałęzi, ale GitHub sprawdzał merge z opublikowanym `main/e1f864c`: audyt AI09
wprowadził m.in. dwa nowe moduły campaign export i kanonizację danych. Ruff,
mypy, docs/runtime/contract checks oraz secrets przeszły. Ciężkie joby nie
wystartowały, a końcowy gate prawidłowo pozostał czerwony.

Scalono opublikowany main wyłącznie do worktree AI12 jako `0800ef2`.
Nowe kandydaty release i smoke `.native-v12-main.v1.json` mają pełny checksum
aktualnej aplikacji; poprzednie manifesty, receipts i etykiety pozostają
zachowane. Graf, schematy, question profile i dependency lockfile'y nie zmieniły
bindingów. Nie wyłączono ani nie osłabiono weryfikatora.

Walidacja:

- 858/858 testów na aktualnym kodzie, w tym native forecast, graf/Assistant,
  autoryzacja, CI guards/reporting, frozen lock i opublikowany audyt AI09.
- Pełne `ci-checks`: Ruff, mypy 655 plików, docs/runtime/kontrakty, fake golden
  50/50 i 36/36 critical, build i Compose config passed.
- Checksum wszystkich 536 plików Python jest identyczny w checkout, wheel
  i faktycznym drzewie merge PR32 `281dd316`. Oba runtime lockfile'y zgadzają
  się w wheel. Gitleaks drzewa passed.
- Plan kolekcji obejmuje 4105 testów / 185 plików w czterech rozłącznych
  shardach; wszystkie 41 native cases są obecne dokładnie raz. Jest to plan,
  nie wykonanie całej kolekcji lokalnie.

Aktualny release to
`agent-evaluation-release-sha256-78a1fed5e7147ba1599f4c9f7c3cc3c0dd21f9dcce8bc057b9ec3e21440960a6`.
Nie wykonano AWS, pełnych eksportów Source ani treningu modeli; nie zmieniono
sąsiednich worktree, procesów lub wspólnych baz/Compose. Nowy publikowany head
nadal wymaga pełnego zdalnego Required CI i otwartych warunków READY AI12.
