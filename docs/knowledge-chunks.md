# Parser Markdown i kandydackie fragmenty — etap 11

Punkt 3 etapu 11 ma wykonywalny parser i deterministyczny chunker.
`chunk-build` waliduje [rejestr korpusu](knowledge-corpus.md), czyta przypięte
obiekty Git i buduje kompletny nowy manifest fragmentów. Dokumenty nadal są
propozycją do przeglądu, a wynik ma wyłącznie `lifecycle=candidate`.
Nie ma aktywnego indeksu ani wyszukiwania.
[Dowody odbioru](evidence/11-chunks.md) obejmują korpus obu repo i czysty checkout.

## Uruchomienie

Z katalogu AI, z RetailOps obok:

```bash
mkdir -p .local/rag
.tools/bin/uv run --locked retailops-ai chunk-build \
  --registry knowledge/corpus.v1.json \
  --chunker-config knowledge/chunker.v1.json \
  --retailops-repo ../retailops-cloud-native-platform \
  --ai-repo . \
  --output .local/rag/chunk-candidate.json
```

Bez `--output` otrzymasz tylko podsumowanie kontroli. Zapis jest atomowy do
nowego pliku `0600`, po pełnej walidacji; istniejący plik nie jest nadpisywany.
Exit 0 oznacza poprawnego kandydata, exit 2 błąd z bezpiecznym kodem.
Treść fragmentów znajduje się w pliku wyjściowym, nie w stdout.
Wyjście należy do lokalnych artefaktów, nie do Git.

Polecenie nie potrzebuje settings serwisu, AWS ani DB; nie fetchuje źródeł,
nie otwiera linków, nie renderuje HTML i nie wykonuje kodu z dokumentów.
Klasy dostępu/statusy/scope/dowody są dziedziczone z zarejestrowanego dokumentu.
Cała treść zachowuje `untrusted_reference`; nie nadaje ról lub narzędzi.

## Jak powstają fragmenty

[knowledge/chunker.v1.json](../knowledge/chunker.v1.json) przypina parser,
reguły transformacji, limit rozmiaru i estymator. `markdown-it-py` **4.2.0**
jest zależnością runtime z uv.lock. Preset to CommonMark z włączonymi tabelami,
bez linkify i typografii. Używamy tokenów oraz ich map linii, bez renderowania.
Podstawa: [API parsera](https://markdown-it-py.readthedocs.io/en/latest/using.html)
i [przypięte wydanie](https://pypi.org/project/markdown-it-py/4.2.0/).

- Nagłówki ATX i setext na poziomie dokumentu tworzą `heading_path` z poziomem
  oraz tekstem. Pominięte poziomy są zachowane jako skok, nie dopisywane.
  Nagłówek wewnątrz kodu, listy lub cytatu pozostaje częścią tego bloku.
  Tekst przed pierwszym nagłówkiem ma pustą ścieżkę i tytuł z rejestru.
- Jeden blok dokumentu jest jednostką fragmentacji: akapit, lista, cytat,
  tabela, kod, HTML lub definicje linków. Nie łączymy różnych sekcji.
  To zachowuje tożsamość sąsiednich bloków po zmianie jednego akapitu.
- Limit domyślny to **2048 bajtów UTF-8**. Większe bloki są dzielone najpierw
  przy końcu linii, następnie spacji, ostatecznie między znakami Unicode.
  Fragment to dokładny wycinek źródła po normalizacji LF, bez dodanych
  wrapperów. Części dużego kodu/tabeli mogą mieć niezamkniętą składnię Markdown.
- Estymacja tokenów to `ceil(utf8_bytes / 4)`, domyślnie do 512 na fragment.
  Jest heurystyką do porównania rozmiaru, nie licznikiem tokenów modelu ani
  gwarancją jego limitów. Adapter embeddings ustali własną transformację
  i walidację rzeczywistego wejścia.

Pominięcia są jawnie zapisane z zakresem źródła i powodem. Obejmują nagłówki
jako strukturę, separatory, samodzielne komentarze HTML, sekcje o dokładnych
nazwach `toc`, `contents`, `table of contents`, `navigation`, `spis treści`,
`nawigacja` oraz bloki składające się wyłącznie z linków do kotwic tego dokumentu.
Zdanie z linkiem lub link zewnętrzny pozostaje treścią. Białe znaki między
blokami nie są fragmentami. Dokument bez treści otrzymuje mapę
`no_retrievable_content`; nie znika z coverage.

Dokładnie takie same fragmenty w tym samym dokumencie, ścieżce nagłówków
i typie bloku są scalane; wszystkie wystąpienia pozostają osobnymi cytatami.
Identyczny tekst w różnych sekcjach lub dokumentach zachowuje osobne chunk IDs
i metadata dostępu/statusu. Near duplicates nie są automatycznie usuwane;
wymagają późniejszego raportu jakości, aby nie zgubić różnic w faktach lub negacji.

## Tożsamość i cytowanie

| Pole | Znaczenie |
|---|---|
| `document_id`, `content_id` | Tożsamość dokumentu i hash jego pełnej znormalizowanej treści |
| `content_checksum` | SHA-256 tekstu fragmentu |
| `chunk_id` | Hash document ID, ścieżki nagłówków, typu bloku, treści i konfiguracji chunkera |
| `chunk_index` | Kolejność pierwszego wystąpienia fragmentu w dokumencie, od 0 |
| `occurrences` | Zakresy znaków i linii każdego wystąpienia oraz source ref do bieżącego SHA |
| `chunker_config_id` | Hash całej przypiętej konfiguracji transformacji |
| `chunk_manifest_id` | Hash pełnego manifestu, korpusu, metadata, fragmentów i cytowań |

Offsety są od 0, końcowy wyłączny, liczone w znakach tekstu po normalizacji
CRLF/CR do LF. Linie są od 1, końcowe włączne. Citation binding ma postać
`git:repo@pełny-SHA:ścieżka#Lstart-Lend`; dokładne offsety rozróżniają części
długiej pojedynczej linii. Ścieżka sekcji jest osobnym polem, więc nie polegamy
na niejednoznacznych kotwicach powtórzonych nagłówków.

Commit, linie, ordinal, czas wykonania i lokalny katalog nie należą do chunk ID.
Po przesunięciu niezmienionego bloku jego ID/checksum pozostają takie same,
a cytat wskazuje nową rewizję i zakres. Zmiana nagłówka lub konfiguracji zmienia
chunk ID; zmiana access/status aktualizuje metadata i manifest, nie sam tekst.
Zmiana dokumentu aktualizuje jego pełny hash również na zachowanych fragmentach.

Każdy build odtwarza zamknięty graf od początku. Usunięty/wyłączony dokument
lub zmieniony blok nie wchodzi do nowego manifestu. Brak nadal zarejestrowanego
pliku blokuje całość. Walidator sprawdza mapy dokumentów, pełną kolejność,
metadata źródła, checksumy, limity, cytaty i brak osieroconych fragmentów.
Hashe i walidacja grafu nie są podpisem autora; budowa dodatkowo sprawdza bajty Git.

## Granice i następny zakres

`make contracts-check` obejmuje także schemas konfiguracji i manifestu chunków.
CI używa rzeczywistych małych Git fixtures, bez AWS lub sąsiedniego repo.
Limit wynosi 4096 unikalnych fragmentów na dokument i 32768 na korpus;
przekroczenie przerywa budowę. Wersja 1 nie udaje pełnego GFM: nie dodaje pluginów
frontmatter, footnotes, task lists ani GitHub alerts; zachowuje ich źródłowy tekst
w ramach dostępnych bloków. Zmiana semantyki parsera wymaga nowej wersji reguł.

[Adapter fake i persistence kandydata](knowledge-index.md) zachowują manifest
fragmentów oraz sprawdzają przestrzeń i checksumy wektorów. Aktywacja/rollback,
filtry retrieval, near duplicates i golden set pozostają
osobnymi bramkami etapu 11. Przegląd redakcyjny korpusu może postępować równolegle.
