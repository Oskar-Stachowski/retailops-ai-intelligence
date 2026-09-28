# Odbiór początku etapu 11 — rejestr i metadane

Pomiar **2026-09-28**, macOS ARM64, Python 3.11.15, uv 0.12.19.
Implementacja: `440b21e99e5815bae8c23886bb0a049d951d2a96`;
baza AI: `7940d2ee7c82dee4e99d49755fc7eb898f15b2ca`.
[Raport JSON](11-corpus.json), [instrukcja i granice](../knowledge-corpus.md).

## Wynik na rzeczywistych źródłach

Wybrane SHA: AI `7940d2ee7c82dee4e99d49755fc7eb898f15b2ca`, RetailOps
`b8de65b53b3f27863bb11999ca6e9a5d6d41a760`. Rejestr obejmuje **20 dokumentów**
(8 AI, 12 RetailOps), 8 odwołań do kodu oraz 2 do dowodów. Proponowane statusy:
10 `specified`, 8 `implemented`, 2 `verified`; wszystkie klasy `public_project`.
W `docs` wykazano 113 wyłączonych Markdown: 112 niezarejestrowanych i 1 ścieżkę
prywatną. Ich treści nie są czytane. Nie wykryto dokładnych duplikatów wybranej treści.

Powtórzenie na odrębnych lokalnych kopiach obu repozytoriów, bez hardlinks,
dało identyczne bajty manifestu. CLI z czystego checkoutu implementacji i nowego
venv odtworzył ten sam wynik. SHA-256 pliku manifestu:
`82d64159aec6dbe8fdcff1de85c36639c5f86f93886c7d498f48f123f99a6a2b`.
Manifest powstał atomowo jako nowy plik `0600`; runtime output pozostaje poza Git.
Logiczne corpus/config IDs i źródłowe SHA są w raporcie.

To pomiar poprawności technicznej **propozycji**. Właściciel przeglądu nie jest
sygnatariuszem akceptacji. Dobór źródeł, zakresy faktów, statusy i access metadata
wymagają przeglądu przed przyszłą aktywacją. Rejestr ma `review_state=proposed`,
a manifest wyłącznie `lifecycle=candidate` i `content_trust=untrusted_reference`.

## Testy i bramki

68 testów korpusu przeszło w 15.10 s. Próby korzystają z rzeczywistych małych
repozytoriów Git, jawnych dowodów JSON i osobnych procesów CLI.

- Determinizm kopii, kolejności źródeł i dirty worktree; `git replace` nie
  zmienia przypiętych danych. Zmiana rewizji zmienia cytowanie, a nie content ID
  niezmienionego dokumentu. Zmieniona treść dotyczy swojego dokumentu;
  brak zarejestrowanego pliku blokuje całą budowę.
- Wyłączenia bez odczytu zakazanej treści; path traversal, URL, `.env`, truth,
  klucze, raw facts i prywatne uploady nie przechodzą jako dokumenty.
  Symlink, executable, błędny UTF-8, NUL, pusty lub zbyt duży blob są odrzucane.
- Brak metadanych, błędne repo/SHA/origin, fałszywe approval i promocja planu
  do verified są odrzucane. Kod musi istnieć w źródłowym snapshot; dowód musi
  mieć zgodne checksum, wynik, commit i datę. Usunięte lub stare odwołanie
  nie zastępuje kodu/dowodu bieżącej zarejestrowanej rewizji.
- JSON Schema Draft 2020-12 i Pydantic, tożsamość oraz coverage manifestu,
  odwołania cytatów, klasy dostępu, exact duplicates i wyłączenia. Duplicate
  JSON keys, NaN, zbyt duże lub głęboko zagnieżdżone wejścia kończą się błędem.
- Nowy plik jest zapisywany dopiero po walidacji; istniejący pozostaje
  niezmieniony. CLI odrzuca prywatne wejście bez wartości, lokalnych ścieżek
  i traceback. Zastosowanie wyłącznie metadanych nie uruchamia auth/retrieval.

`make bootstrap ci-local` — exit 0, **341 passed in 22.80s**.
Czysty checkout commitu implementacji, nowe venv i ten sam lockfile:
`make bootstrap ci-local` — exit 0, **341 passed in 26.98s**, bez pominięć.
Ruff/format, Mypy strict (53 pliki), docs, trzy snapshot gates, wheel/sdist,
Compose config i oba Gitleaks przechodzą. Actionlint przechodzi w obu checkoutach.
Celowo osłabiony schema w osobnej kopii powoduje exit 1 gate; oryginał pozostaje
niezmieniony. Czysty checkout po kontrolach zachowuje pusty status Git.

## Ograniczenia odbioru

To początek punktów 1–2 etapu 11, nie odbiór całego RAG. Walidator wiąże zapisane
dowody z SHA, ale nie powtarza historycznych pomiarów ani nie ocenia prawdziwości
tekstu. Nie ma parsera sekcji, near duplicates, chunków, embeddings, indeksu,
retrieval, golden set, aktywacji lub rollback. Access metadata nie dowodzą
filtrów principal/cache. Nie mierzono jakości semantycznego wyszukiwania.

Nie zmieniono zależności ani migracji DB. Pełnego Compose persistence smoke
nie powtarzano; wcześniejszy odbiór etapu 01 zachowuje własny zakres i datę.
Commity tego zakresu pozostają lokalne, bez nowego push i odbioru Required CI.
Następny zakres oraz przegląd redakcyjny opisuje [status](../STATUS.md).
