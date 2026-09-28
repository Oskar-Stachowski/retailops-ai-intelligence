# Korpus wiedzy — początek etapu 11

Etap 11 ma lokalny rejestr kandydacki i walidator źródeł Git. Obejmuje to
granice korpusu oraz metadane statusu, czyli pierwszy zakres punktów 1–2 planu.
[Parser i chunker](knowledge-chunks.md) realizuje kolejny zakres punktu 3.
[Fake embeddings i zapis kandydata](knowledge-index.md) zachowują ten korpus
w pgvector. [Lifecycle](knowledge-lifecycle.md) obsługuje kwalifikację,
atomowy wskaźnik i rollback syntetycznych indeksów testowych. Wyszukiwanie RAG
i aktywacja rzeczywistego korpusu pozostają następnymi zakresami.
[Bieżący status](STATUS.md), [dowody rejestru](evidence/11-corpus.md).

## Co jest zarejestrowane

[knowledge/corpus.v1.json](../knowledge/corpus.v1.json) jawnie wymienia 20 plików
Markdown: 12 z RetailOps i 8 z RetailOps AI. Każde repo ma pełny źródłowy SHA,
każdy dokument — checksum oryginalnych bajtów, tytuł, typ, `access_class`,
`document_status` oraz ograniczony `fact_scope`. Katalog `docs` jest granicą
przeglądu; tylko dokładnie wymienione pliki stają się kandydatami. Dodanie pliku
do `docs` nie dodaje go automatycznie do korpusu.

Rejestr ma `review_state=proposed`, właściciela przeglądu `Oskar-Stachowski`
i środowisko `local`. Właściciel jest wskazaniem odpowiedzialności, a nie
zapisem udzielonej akceptacji. Dobór plików, zakresy faktów, klasy dostępu
i statusy wymagają przeglądu redakcyjnego przed późniejszą aktywacją.
Walidacja techniczna nie zatwierdza treści. Wersja 1 pozwala wygenerować tylko
manifest `candidate`; odrzuca deklaracje `approved` lub `active`.

## Statusy i dowody

| Status | Znaczenie i kontrola |
|---|---|
| `specified` | Opis lub plan; wszystkie `docs/plans/` mają ten status |
| `implemented` | Ograniczony zakres opisu ma wskazany plik kodu z SHA i checksumą |
| `verified` | Zakres ma wersjonowany dowód JSON, wynik, commit i datę pomiaru |
| `deprecated` | Źródło wycofywane; nie stanowi dowodu aktualnego działania |
| `historical` | Informacja historyczna, odnoszona do swojej rewizji |

Odwołania do kodu i plików dowodowych muszą pochodzić z tych samych źródłowych
rewizji co zarejestrowane repozytoria. Dowód może opisywać wcześniejszy commit
pomiaru: walidator sprawdza jego istnienie oraz jawne wskaźniki JSON wyniku,
daty i commitu. Sprawdza spójność zapisanych danych, nie powtarza historycznej
próby ani nie poświadcza prawdziwości dowolnego JSON.

Status nadaje przeglądający człowiek lub kontrolowany pipeline, nigdy LLM.
`fact_scope` ogranicza twierdzenie: przykładowo karta RF opisuje diagnostyczny
model `rejected`, bez prawa do serving. `implemented` oznacza obecność wskazanego
kodu, bez gwarancji wdrożenia lub odbioru runtime. Dla przyszłego pytania „co
działa?” retrieval musi odróżniać te stany i ujawniać sprzeczne dowody. Obecny
walidator nie generuje odpowiedzi.

## Walidacja lokalna

Z katalogu AI, z drugim repo obok:

```bash
mkdir -p .local/rag
.tools/bin/uv run --locked retailops-ai corpus-check \
  --registry knowledge/corpus.v1.json \
  --retailops-repo ../retailops-cloud-native-platform \
  --ai-repo . \
  --output .local/rag/corpus-candidate.json
```

Bez `--output` polecenie tylko waliduje i wypisuje małe podsumowanie JSON.
Zapis następuje dopiero po wszystkich kontrolach, atomowo do nowego pliku
z uprawnieniami `0600`; istniejący plik nie jest nadpisywany. Wybierz nową
nazwę dla kolejnego manifestu. Exit 0 oznacza poprawnego kandydata, exit 2 —
błąd; komunikat nie zawiera wartości wejściowych, ścieżek lokalnych ani Git stderr.

Potrzebne są lokalne obiekty Git przypiętych commitów i origin zgodny z nazwą
jednego z dwóch repozytoriów. Polecenie nie fetchuje i nie używa AWS, DB ani
konfiguracji serwisu. Kontrola origin zapobiega przypadkowemu pomyleniu checkoutów;
nie zastępuje zaufanego pobrania repo ani kryptograficznej akceptacji autora.
Lokalne katalogi repo i ścieżkę wyjścia wybiera zaufany operator CLI.

Czytane są bloby z przypiętej rewizji Git, z wyłączonym `git replace`.
Niezacommitowane zmiany i untracked files nie trafiają do manifestu. Nie ma
rekurencyjnego odczytu plików worktree. Wybrany Markdown musi być zwykłym,
niepustym UTF-8 blobem do 500 KB, bez NUL; symlink, executable i submodule
powodują błąd. Brak wybranego pliku lub błędna checksuma przerywa całą budowę.

## Tożsamości i wyłączenia

`document_id` zależy od repo i ścieżki; `content_id` od UTF-8 po normalizacji
CRLF/CR do LF. Oryginalna checksuma bajtów pozostaje osobnym polem.
`source_ref` wskazuje dokładne repo, pełny commit i ścieżkę. Niezmieniona treść
zachowuje content identity po zmianie rewizji, a cytowanie zmienia binding.
`corpus_config_id` obejmuje uporządkowany rejestr, statusy i zakresy dostępu;
`corpus_id` obejmuje cały manifest, źródła i wyłączenia. Lokalna ścieżka checkoutu
oraz czas wykonania nie zmieniają tożsamości.

`.env`, ukryte ścieżki, klucze, logi, raw facts, simulation truth, prywatne
uploady, binaria, arbitrary URL i ścieżki poza `docs` są niedopuszczalne jako
dokumenty. Nazwy niezarejestrowanych Markdown w dozwolonych katalogach są
raportowane wraz z powodem, bez czytania treści. Pozostałe obszary są wyłączone
przez jawną `exclusion_policy`; nie powstaje inwentarz ich zawartości.
JSON evidence jest wyłącznie źródłem kontroli metadanych, nie tekstem korpusu.
Adversarial fixtures pozostają poza zwykłym korpusem.

Klasy `public_project`, `project_internal`, `restricted` są metadanymi.
Środowisko jest częścią tożsamości korpusu; nie ma użytkowego retrieval.
Wskaźnik `offline_test` służy wyłącznie próbom lifecycle w `test`.
Filtry principal/access, cache i izolacja runtime wymagają
osobnego odbioru przy retrieval. Manifest zawsze oznacza treść jako
`untrusted_reference`; treść dokumentu nie nadaje narzędzi ani uprawnień.

Raportujemy dokładne duplikaty dokumentów po normalizacji LF. Pomijanie nawigacji,
heading paths i usuwanie osieroconych fragmentów realizuje [chunker](knowledge-chunks.md).
Near duplicates pozostają późniejszą kontrolą jakości. Document IDs nie są chunk IDs.

## Kontrakty i następny zakres

[JSON Schemas](../contracts/knowledge/v1/) powstają z modeli Pydantic.
`make contracts-check` sprawdza snapshots i strukturę rejestru; relacje między
SHA, plikami i dowodami wykonuje `corpus-check`. CI sprawdza te relacje na
rzeczywistych, małych repozytoriach testowych i nie wymaga sąsiedniego checkoutu.
Zmiana źródłowego SHA wymaga ponownego związania checksum i odwołań oraz
przeglądu propozycji.

Następny zakres: ograniczony retrieval, golden set i filtry access/status.
Przegląd redakcyjny oraz odświeżenie źródeł kandydata mogą odbywać się równolegle.
Użytkowa aktywacja wymaga tych odbiorów; fake walidacja nie otwiera tej bramki.
