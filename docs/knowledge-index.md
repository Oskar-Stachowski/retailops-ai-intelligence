# Kandydacki indeks fake embeddings — etap 11

Punkt 4 etapu 11 ma adapter offline oraz zapis kompletnego kandydata w izolowanej
bazie PostgreSQL AI z pgvector. Wynik zachowuje [korpus](knowledge-corpus.md),
[fragmenty i cytaty](knowledge-chunks.md) oraz przypiętą konfigurację embeddings.
Fake vectors służą odbiorowi kontraktów i persistence; nie opisują znaczenia
tekstu. Nie potwierdzają jakości wyszukiwania. Nie ma aktywnego indeksu,
endpointu retrieval ani wywołań Bedrock. [Lifecycle testowy](knowledge-lifecycle.md)
sprawdza osobny wskaźnik na syntetycznych indeksach, bez aktywacji tego korpusu.

## Budowa offline

```bash
mkdir -p .local/rag
.tools/bin/uv run --locked retailops-ai index-build \
  --registry knowledge/corpus.v1.json \
  --chunker-config knowledge/chunker.v1.json \
  --embedding-config knowledge/embeddings.fake.v1.json \
  --retailops-repo ../retailops-cloud-native-platform \
  --ai-repo . \
  --output .local/rag/index-candidate.json
```

CLI ponownie sprawdza zarejestrowane bloby Git, buduje fragmenty i tworzy jeden
zamknięty artefakt: manifest indeksu, pełny manifest fragmentów z korpusem oraz
unikalne embedding records. Hash indeksu wiąże wszystkie fragmenty z ID i
checksumą wektora; walidator sprawdza pełne coverage i dziedziczoną metadata.
Build nie wymaga settings, DB, AWS ani sieci. Zapis jest atomowy do nowego
pliku `0600`; istniejący plik nie jest nadpisywany. Artefakt pozostaje poza Git.
Stdout zawiera tylko ID, liczniki i status. Exit 2 oznacza błąd.

## Przypięta przestrzeń i cache

[Konfiguracja](../knowledge/embeddings.fake.v1.json) ma provider `fake`, model
`sha256-unit-f32-v1`, region `offline`, brak inference profile, wymiar **32**,
normalizację L2 do 1, odległość `cosine`, transformację `utf8-chunk-body-v1`
i zapis `float32-big-endian-v1`. Wspierane wymiary to 8, 16, 32, 64.
Inny provider/model/region/transformacja wymaga nowego kontraktu i adaptera.

Algorytm wyprowadza komponenty z SHA-256 space ID, checksumy tekstu i numeru
komponentu, normalizuje wektor i zaokrągla do float32. Nie zależy od czasu,
katalogu checkoutu ani losowego hash seed Pythona. Wejściem jest dokładny tekst
fragmentu po transformacji chunkera, bez nagłówka/cytatu dodawanego do tekstu.
Zmiana tej reguły wymaga nowej wersji transformacji.

| Tożsamość | Zawartość hasha |
|---|---|
| `space_id` | Cała resolved konfiguracja embeddings |
| `embedding_id` | Checksum tekstu i space ID |
| `vector_checksum` | Bajty komponentów float32 w porządku big endian |
| `index_id` | Cały manifest indeksu z corpus/chunk manifest IDs i mapą chunk → embedding/checksum |

Cache w pipeline i upsert w DB wykorzystują body hash oraz konfigurację.
Identyczny tekst w różnych dokumentach ma jeden wektor, ale zachowuje odrębne
chunk IDs, metadata i cytaty. Zmiana access/status/źródłowej rewizji tworzy nowy
indeks i nie zmienia wektora niezmienionej treści. Zmiana wymiaru tworzy osobną
przestrzeń oraz nowe embedding/index IDs. Nie mieszamy przestrzeni.
Usunięty fragment nie należy do nowego indeksu; stare kandydaty i ich cache
pozostają niemodyfikowalne. Garbage collection wymaga osobnego lifecycle.

## Zapis w bazie AI

`index-store --candidate PATH [--env-file PATH]` korzysta z settings serwisu.
`DATABASE_URL` musi wskazywać `retailops_ai` jako `ai_app`; środowisko kandydata
musi odpowiadać `APP_ENV`. Fake candidates są dopuszczone tylko w `local/test`.
Nie przekazuj URL z hasłem w argv ani nie publikuj portu DB. W Compose można
użyć istniejącej usługi maintenance i przekazać artefakt przez stdin:

```bash
make compose-up
.tools/bin/uv run --locked python scripts/local_stack.py rag-store \
  --candidate .local/rag/index-candidate.json
```

Kontroler waliduje plik i przekazuje go przez stdin do CLI w kontenerze;
nie wypisuje tekstu dokumentów ani poświadczeń. Kolejny zapis identycznego
kandydata zwraca `already_present`, bez zmiany wierszy.

Migracja **0002_rag_candidates** tworzy cztery tabele w `ai`:
`rag_embedding_spaces`, `rag_embeddings`, `rag_indexes`, `rag_index_chunks`.
Pole manifestu `migration_revision=0002_rag_candidates` przypina kontrakt storage.
Store wymaga aktualnego head **0003_rag_lifecycle** i pgvector **0.8.6**;
nie migruje bazy i nie zmienia istniejących manifestów/IDs po dodaniu lifecycle.
Komponenty wektora są natywne `vector`, a wymiar kontrolują CHECK oraz złożone
FK przestrzeni/środowiska/indeksu. DB sprawdza normę i checksumę rzeczywistych
bajtów float32. Fragment musi odpowiadać właściwej pozycji manifestu, tekstowi,
embedding ID i checksumie. Deferred constraint odrzuca niekompletny indeks
przy commitcie transakcji. Wszystkie cztery tabele blokują UPDATE/DELETE.

Space, nowe cache records, indeks i fragmenty są zapisywane w jednej transakcji
z advisory lock, ograniczonym czasem oczekiwania i zapytań. Nieudany zapis
wycofuje wszystkie nowe wiersze. Konflikt istniejącego cache/ID kończy się błędem;
nie nadpisujemy innej zawartości. Odczyt po zapisie odtwarza i waliduje artefakt.
Hashe i CHECK nie są podpisem autora ani zatwierdzeniem korpusu; operator
przekazuje artefakt z zaufanej lokalnej budowy. Rola będąca właścicielem bazy
i administrator Docker pozostają granicą zaufania developmentu.

Nie ma ANN index, automatycznej promocji ani usuwania starego kandydata.
Osobny pointer testowy opisuje [lifecycle](knowledge-lifecycle.md).
Późniejsze retrieval będzie musiało najpierw ustalić zatwierdzony
indeks, środowisko, przestrzeń i access/status filters przed rankingiem.

Podstawa SQL: [pgvector 0.8.6 — typy, wymiary i funkcje](https://github.com/pgvector/pgvector/tree/v0.8.6),
[format vector_send w przypiętej wersji](https://github.com/pgvector/pgvector/blob/v0.8.6/src/vector.c),
[PostgreSQL 16 — SHA-256 bajtów](https://www.postgresql.org/docs/16/functions-binarystring.html),
[złożone FK](https://www.postgresql.org/docs/16/ddl-constraints.html)
oraz [transakcje Psycopg](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).

## Odbiór i dalsza praca

`make check` obejmuje adapter fake, kontrakty, cache, tampering i CLI.
`make compose-smoke`, wymagane przez job persistence Required CI, sprawdza
rzeczywisty pgvector: round trip, ograniczenia SQL, ponowny zapis, współbieżność,
wycofanie awarii, zmianę/usunięcie treści i trwałość po SIGKILL oraz down/up.
[Dowody](evidence/11-index.md) opisują korpus 302 fragmentów, 433 testy i odbiór
na rzeczywistym pgvector ze świeżego checkoutu.

[Lifecycle](knowledge-lifecycle.md) wiąże jawne zgody i walidację oraz odbiera
atomowy swap/rollback w testach. Dalej: retrieval z uprawnieniami i filtrami,
near duplicates, golden set i ocena jakości. Adapter Bedrock wymaga później
ograniczonego zakresu etapu 12 i rzeczywistego odbioru modelu/dimension/region.
