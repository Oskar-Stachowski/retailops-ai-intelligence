# Aktualny status

**2026-09-28 · fundament etapu 01 odebrany lokalnie i w zdalnym Required CI.**

**Etap 11 w realizacji:** [korpus wiedzy](knowledge-corpus.md) ma rejestr
kandydacki 29 dokumentów z obu repo, przypięte SHA/checksums, klasy dostępu,
statusy i kontrolę dowodów. Techniczna walidacja offline nie oznacza akceptacji
redakcyjnej ani aktywnego RAG; obecne zgody właściciela zapisano osobno.
[Odbiór](evidence/11-corpus.md) potwierdza 341 testów, czysty checkout
i identyczny manifest odtworzony z kopii repozytoriów.
[Parser/chunker](knowledge-chunks.md) zachowuje heading paths, stabilne chunk IDs,
zakresy cytatów i pełne mapy dokumentów; nowy build usuwa nieaktualne fragmenty.
[Odbiór chunków](evidence/11-chunks.md): 302 fragmenty z 20 dokumentów,
389 testów i identyczny wynik odtworzony z czystego checkoutu.
[Fake embeddings i indeks pgvector](knowledge-index.md) mają przypiętą przestrzeń,
cache treści oraz transakcyjny zapis niemodyfikowalnego kandydata.
Fake vectors nie potwierdzają jakości semantycznej.
[Odbiór indeksu](evidence/11-index.md): 302 wektory, 433 testy, identyczny artefakt
z czystego checkoutu i realny Compose z awariami/transakcjami pgvector.
[Lifecycle indeksu](knowledge-lifecycle.md) dodaje jawne zgody, walidację,
atomowy wskaźnik, retry i rollback w osobnym kanale syntetycznych testów.
Użytkowa aktywacja jest blokowana do golden evaluation; rzeczywisty korpus
ma osobne zgody właściciela i nie ma aktywnego retrieval.
[Odbiór lifecycle](evidence/11-lifecycle.md): 463 testy, czysty checkout,
konkurencyjna aktywacja, rollback i zachowany pełny pin po restartach PostgreSQL.
[Retrieval i golden set](knowledge-retrieval.md) dodają exact cosine pgvector,
deterministyczną selekcję, bounded context, grant knowledge:read, filtry
repo/type/status/access, live deny dla starych pinów i 44 wersjonowane pytania.
Ścieżka runtime działa na kwalifikowanych indeksach testowych; rzeczywisty
korpus i etykiety mają osobne zgody właściciela. Fake report nie otwiera aktywacji.
[Odbiór retrieval](evidence/11-retrieval.md): 498 testów, świeży PG/HTTP,
7/7 krytycznych przypadków oraz jawnie nieprzechodzące progi Recall@5/MRR z fake.

[Administracja indeksami](knowledge-administration.md) dodaje osobny grant,
trwałe runy/idempotencję, odczyt current i worker zatwierdzonych snapshotów
`test/fake`. Run zapisuje kandydata i raport; nie aktywuje indeksu.
Zatwierdzone profile golden rozszerzają worker; użytkowa kwalifikacja wymaga odbioru jakości.
[Odbiór administracji](evidence/11-administration.md): 542 testy, rzeczywisty
PG/HTTP, idempotencja, wznowienie workera po SIGKILL i retencja po restartach.

[Kontrola podobnych treści](knowledge-review.md) ma deterministyczny raport
dokładnych/near powtórzeń z cytatami i różnicami statusów/access, bez scalania
lub usuwania treści. [Odbiór](evidence/11-similarity.md): 573 testy, identyczny
raport z czystych klonów i zgodność z pełnym porównaniem wszystkich par.
W przypiętym korpusie 20 dokumentów/302 fragmentów brak kandydatów przy progu
leksykalnym 0,80; nie jest to potwierdzenie aktualności lub jakości semantycznej.

[Odświeżenie źródeł i etykiet](knowledge-sources.md) wiąże snapshoty AI `082bed4`
i RetailOps `78f801f`. [Odbiór](evidence/11-sources.md): 29 dokumentów/451 fragmentów,
578 testów, 44 pytania, walidacja obu list sekcji i 9/9 przypadków krytycznych;
czysty checkout odtwarza identyczny indeks oraz raport podobieństwa.
Statusy i access istniejących pozycji zachowano, zakresy twierdzeń zawężono;
aktualny raport podobieństwa ma jedną uzasadnioną kopię krótkiego wprowadzenia.
Fake Recall@5/MRR nie przechodzą; źródła i etykiety mają osobne zgody właściciela.

[Kontrola przed kwalifikacją](knowledge-qualification.md) odtwarza golden wyniki,
wiąże osobne zgody na korpus/etykiety i sprawdza kompletność decyzji podobieństwa.
[Odbiór](evidence/11-qualification.md): 611 testów i identyczny manifest z czystych
klonów. Pomiar poprzedza zgody właściciela; preflight fake nie otwiera aktywacji
również po przekazaniu obu zgód.

[Zatwierdzone profile golden](knowledge-golden-jobs.md) wiążą obecny korpus,
44 pytania, zamrożone progi i obie zgody. Worker działa w `local/test`, a
niezaliczony próg zachowuje pełny raport `failed/gate_failed` bez outputu.
[Odbiór](evidence/11-golden-jobs.md) obejmuje bramkę PostgreSQL, HTTP,
prywatny eksport i retencję raportów. Nie nadaje użytkowej aktywacji.

**Postęp etapu 11: około 96%** — szacunek zakresu implementacji, z uwzględnieniem
niezamkniętych odbiorów. Rejestr/metadata, parser, fake/storage, test lifecycle
i ograniczony retrieval są zaimplementowane. Punkt 7 ma golden set i raport,
administracyjne runy obejmują zatwierdzony korpus i pomiar golden fake.
Odbiór jakości i użytkowa kwalifikacja pozostają otwarte.

Pakiet, settings, CLI i diagnostyczny HTTP działają razem z izolowanym
PostgreSQL AI/pgvector i oddzielną bazą/rolą MLflow.
[Uruchomienie](local-stack.md), [HTTP](http-service.md).

[Lokalne uprawnienia API](access-control.md) weryfikują Bearer token z prywatnej
mapy, principal i cały scope; endpointy identity, forecast preflight i admin metadata
mają testy 401/403 oraz rzeczywisty proces loopback. [Dowody](evidence/01-access.md)
opisują odbiór i restart wymagany po zmianie polityki.

[Kontrakty v1](data-contracts.md) są wykonywalne: dataset/feature/label/split/model/
prediction/run/tool/bundle, JSON Schema, syntetyczne fixtures i walidacja offline.
[Dowody](evidence/01-contracts.md) opisują testy struktury, PIT, lineage, identity,
stanów runa i bezpiecznych tool results. Nie ma jeszcze importera, treningu ani
endpointów predykcji. Schemat runa ML/tool nie oznacza workera treningu/agenta;
administracyjne runy RAG mają osobny worker testowy.

[Odbiór persistence](evidence/01-persistence.md) opisuje rzeczywisty Compose,
crash/restart, DB outage/recovery i zachowanie artefaktów.
[HTTP](evidence/01-http.md) i [fundament](evidence/01-foundation.md) zachowują zakres
wcześniejszych pomiarów. Required CI obejmuje kod, schemas, docs i persistence.
[Zdalny odbiór](evidence/01-remote-ci.md) potwierdza success Required CI po
push na main obu repozytoriów. W AI przechodzą checks, secrets, persistence
i required-result; main wymaga PR i aktualnej gałęzi również dla administratora.

## Następna praca

Główny kierunek: dane etapu 02 w RetailOps.
Drugi strumień na `ai/rag-corpus`: odbiór jakości i polityka użytkowej
kwalifikacji etapu 11. Zgody na obecny korpus/etykiety zapisano;
użytkowa aktywacja wymaga jeszcze odbioru jakości. Real embeddings/Bedrock smoke i agent są w etapie 12.
Praca może postępować równolegle ze strumieniem danych. Etap 03 wymaga odbioru
źródła z 02, a forecasting i serving kolejnych bramek.

Token metryk nie jest systemem tożsamości użytkowników. MLflow ma lokalną
izolację sieciową, bez aplikacyjnego auth. Pipeline danych, modele, wyszukiwanie
użytkowe RAG, agent, streaming i cloud mają własne późniejsze bramki.
