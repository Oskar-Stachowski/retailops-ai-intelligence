# AI 03.4 — typed importer odebrany lokalnie

**2026-09-29.** Branch `ai/03-04-importer`, osobny worktree
`/private/tmp/retailops-ai-03-04`, baza `4376cb8` (handoff 03.3 + main RAG `abf3f69`).
Implementacja **`aadd145e6f023078dcc34783247213d6fe8fdde5`**.
Worktree `ai/12-tools` nie był modyfikowany przez tę pracę.
[Runbook](../source-snapshot-import.md), [rejestr pomiarów i checksum](03-04-importer.json)
oraz [karta danych](../cards/source-snapshot-import.md) opisują granice odbioru.

**706 testów passed**, bez failures/errors/skips, w tym 48 nowych testów.
Ruff/format dla 180 plików, strict Mypy dla 110, dotychczasowe kontrakty
intelligence/access/knowledge, docs/Required CI checker, wheel/sdist,
Compose config oraz Gitleaks staged przechodzą. Sandbox blokował dwa
istniejące testy real loopback HTTP; pełny udany odbiór wykonano poza sandboxem.
Nie pomijano tych testów ani nie obniżano bramek.

## Zachowanie

Osobny `retailops-ai-snapshot` ma `verify`, `import` i `verify-import`.
Przyjmuje supported source/snapshot/handoff, nie tylko jeden znany fixture.
Waliduje manifest/lineage, pełny inventory, wszystkie fizyczne SHA/size,
Arrow schema/types/nullability/metadata, decoded counts, grain, ranges,
partitions i source multiset canonical hashes. Nie importuje generatora,
operacyjnej DB ani API. Extra `snapshot` jest oddzielny od podstawowego runtime.

Przed publikacją weryfikowany jest sealed staging, nie zmienne wejście.
Fsync i no-replace rename odmówiły zastąpienia nawet pustego destination.
Identyczny reimport i poprawny alternatywny layout/execution metadata zachowują
wszystkie istniejące bajty. Konflikt snapshotu pod tym samym source ID,
uszkodzona lub niekompletna istniejąca publikacja blokują import.
Dwa równoczesne importy dają dokładnie jeden `published` i jeden `reused`.

Byte corruption/missing/extra/directory/symlink/FIFO, unsupported version,
traversal/absolute paths, fake ID, zła klasyfikacja i niegotowy use case
są odrzucane. Test ze zmienionym Parquet i poprawionym byte SHA nadal odpada
na typed hash. Osobne próby odrzucają schema/metadata drift, empty required value,
duplicate grain, fałszywy count/date range i nieprawidłowy dzień partition.
Zmiana partycjonowania prawidłowych wierszy zachowuje source/snapshot ID.

Obsłużona awaria nie zostawia publikacji ani stagingu. Test **SIGKILL przed
rename** zostawił wyłącznie prywatny ukryty staging 0700; retry opublikował
poprawny snapshot. Nie ma automatycznego usuwania katalogów aktywnych procesów.

## Rzeczywiste eksporty i pomiary

Dwa świeże procesy na wariant wykonały import → reimport → verify,
sprawdzając niezmienność wejścia/publikacji i limity 300 s / 1024 MiB.
Python 3.11.15, PyArrow 25.0.1, macOS arm64. Benchmark obejmuje konsumenta,
nie generator → curated. Code-file hashes i lock są zgodne z `aadd145`.

| Wejście | Tabele / wiersze | Pliki / bajty snapshotu | Czas przebiegu | Max RSS |
|---|---:|---:|---:|---:|
| [ai-smoke fixture](03-04/fixture-import.json) | 25 / 31171 | 74 / 1967631 | 4,16–4,26 s | 76,83 MiB |
| [ai-temporal-smoke, partition threshold 100](03-04/temporal-import.json) | 25 / 52973 | 1211 / 9533017 | 15,20–18,46 s | 62,47 MiB |
| [ai-smoke z prawdziwym optional truth](03-04/truth-import.json) | 29 / 32759 | 83 / 2129701 | 5,47–7,26 s | 75,69 MiB |

Fixture zachowuje source/exporter `6561481`. Dwa dodatkowe warianty są
rzeczywistymi kwalifikowanymi eksportami RetailOps `b5d2804` z tego samego
source generatora 2.6, seed 42, end date 2026-07-31. Temporal wymuszał
partition threshold 100, obejmując również order-item mapping. Source ID
smoke i temporal oraz snapshot ID facts odpowiadają wcześniejszemu odbiorowi.
Truth tworzy odrębny snapshot ID pod tym samym source; pełne IDs podają JSON-y.
Wygenerowane dane pozostają poza Git; fixture i archiwa nie zostały powiększone.

Default odrzuca truth snapshot. Jawny opt-in przyjmuje wyłącznie cztery
znane truth tabele pod `evaluation_truth`, z prywatnymi modes; nie tworzy
facts ani joinów/API/mountów. Unit test używa czterech syntetycznych wierszy
do sprawdzenia izolacji namespace, a osobny pomiar powyżej prawdziwego eksportu.
Source reports są kontrolowane, nie regenerowane przez konsumenta.

## Przenośność i kolejne zakresy

[Odłączony wheel](03-04/wheel-probe.json) zawiera moduły importera, kontrakt,
schema i lock. `python -I` w katalogu bez obu checkoutów dwukrotnie importuje
skopiowany fixture (`published`, `reused`), bez generatora/SQLAlchemy/API.
Testy odłączonego minimalnego pakietu potwierdzają tę samą niezależność.

Production `publish.py` i `files.py` przeszły też probe **Linux aarch64**
w pinned Python 3.11.15, bez sieci, readonly filesystem, jako 65534.
Kernel odmówił nadpisania pustego celu, a poprawny katalog opublikował w całości.
Pełny Linux/PyArrow pipeline czeka na Required CI; nie deklarujemy jego odbioru
na podstawie samego probe atomowości.

Można przejść do **03.5 — curated**. Mapping/quarantine/as-of reads i pełny
cross-repo flow wymagają 03.5/03.6; **04/06 nadal czekają**. Smoke nie kwalifikują
ai-dev/ai-training ani modeli. Nie wykonano nowych wywołań AWS, treningu,
push, zdalnego merge ani Required CI. Commity są lokalne i niezależne od AI 12.
