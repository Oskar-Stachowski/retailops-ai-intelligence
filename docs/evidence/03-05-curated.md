# AI 03.5 — normalized curated i historyczny odczyt

**Odbiór lokalny: 29.09.2026.** Branch `ai/03-04-importer`, osobny worktree
`/private/tmp/retailops-ai-03-04`. Implementacja `4e4717b`, blokada niejednoznacznych
planów `073b22e`. AI 12 pozostał w swoim niezależnym worktree.
[Runbook](../curated.md), [karta danych](../cards/curated.md),
[pełne metadane odbioru](03-05-curated.json).

**743 testy passed**, w tym 37 nowych, bez failures/errors/skips.
Ruff/format (194 pliki), strict Mypy (117), kontrakty intelligence/access/knowledge,
docs/Required CI contract, wheel/sdist, Compose config i staged Gitleaks przechodzą.
Pełna regresja wymagała loopback poza sandboxem; pominięć nie stosowano.
Rzeczywisty target `make curated-check` przeszedł przez locked uv/extra snapshot;
pozostałe targety były pominięte w tym powtórzeniu Make po ich osobnym odbiorze.

Builder seal/verify przyjmuje import source 2.6.0, normalizuje 25 typed facts/plans,
uzgadnia produkty/selling/channel/stock przez jawne katalogi i wersjonowane
assignment/route/assortment. Nie wybiera zastępczego store/warehouse ani zera.
Każdy rejection zachowuje source row/grain/hash/reason i blokuje ready publication;
oddzielny curated-rejected bundle jest diagnostyką z CLI exit 2.

Curated ID obejmuje parents, config/watermarks, exact code/schema/lock fingerprints,
Python/PyArrow oraz canonical typed content, counts/grain/ranges/quarantine.
Writer layout nie zmienia ID. Rebuild zachowuje istniejące bajty; kernel no-replace
nie nadpisuje nawet pustego lub uszkodzonego destination.

| Wejście | Curated tabele / wiersze | Pełny consumer przebieg, dwukrotnie | Max RSS |
|---|---:|---:|---:|
| fixture | 25 / 31171 | 49.59–66.75 s | 104.22 MiB |
| temporal | 25 / 52973 | 122.14–134.65 s | 100.53 MiB |
| truth | 25 / 31171 | 65.03–70.83 s | 106.08 MiB |

Każdy fresh process mierzy import → build → rebuild → verify → dwa as-of
porównane niezależnie z raw source quantity versions; limit 300 s / 1024 MiB.
Fixture i [jego pełny manifest](03-05/fixture-curated-manifest.json) mają 31171
wierszy, temporal 52973; wszystkie 25 tabel i wersje zachowano, quarantine = 0.
Truth input ma 29 tabel/32759 wierszy, ale curated nadal 25/31171, bez truth.
Reports: [fixture](03-05/fixture-curated.json), [temporal](03-05/temporal-curated.json),
[truth](03-05/truth-curated.json).

Inputs są przypiętymi eksportami: fixture producenta `6561481`, temporal/truth
z 03.4 producenta `b5d2804`, seed 42/end 2026-07-31. Nie wykonywano nowej
generacji/kwalifikacji upstream. Pomiary są lokalne; inne kontrole mogły pracować
równolegle, RSS dotyczy każdego worker process, nie sumy procesów.

Testy obejmują exact decimal bez zależności od context, NFC/UTC, missing/late/
ambiguous mapping, data-gap vs explicit zero/closed, version gap i availability
regression, future known plans, mikrosekundowy cutoff i późniejszą korektę,
conflicting highest plan versions, forged byte SHA, path/schema/version faults,
concurrency, kontrolowaną awarię, SIGKILL/hidden private staging/retry, truth i
przerwanie write budget. Source i gotowy katalog pozostają niezmienione.

[Odłączony wheel](03-05/wheel-probe.json) przez `python -I` ładuje własny
package/schema/lock i wykonuje import/build/rebuild/verify/as-of bez generatora,
API i SQLAlchemy. Jego curated ID i exact fingerprints są identyczne z fresh
fixture acceptance. Fixture curated jest zachowany poza Git w
`data/generated/curated/curated-sha256-6d7c6088199cd0505ad5a91ff0fbca171960e900e4a00f2e3dfb55a0617cade3/`.

Statyczne/legacy rekordy bez recorded availability pozostają jawnie nieznane.
Curated nie tworzy features/labels/modeli; inventory false i dalsze ML/replay
use cases not_ready. Truth opt-in dotyczy parent import, nie dostępu do features.
Ten sam UID nie jest izolowany uprawnieniami. SIGKILL może zostawić prywatny
staging; owner sprawdza aktywne procesy przed cleanup. Pełny Linux oraz
ai-dev/ai-training nie były kwalifikowane.

**Można rozpocząć 03.6** — pełny generator → qualification → export → import →
curated z przypiętymi rewizjami obu repo i całkowitym resource gate. 04/06
pozostają zamknięte. Push i zdalne Required CI nie były wykonywane.
