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
[Odbiór](evidence/11-golden-jobs.md): 627 testów, bramka PostgreSQL/HTTP,
prywatny eksport i retencja raportów. Czyste klony odtwarzają identyczny profil.
Run właściwego korpusu ma 9/9 krytycznych kontroli, lecz nie przechodzi
Recall@5/MRR; zachowuje raport bez outputu i użytkowej aktywacji.

**[Audyt Etapu 11](evidence/11-audit.md): zakres offline gotowy do PR;
pełny etap pozostaje otwarty.** Nie znaleziono błędów blokujących scalenie tego
zakresu. Nowa regresja 104 testów i odtworzenie golden potwierdzają wcześniejszy
odbiór 627 testów. Przed merge wymagany jest zielony zdalny Required CI dla
tego branchu. Do pełnego odbioru brakuje ścieżki rzeczywistego providera,
użytkowej kwalifikacji/aktywacji oraz potwierdzenia jakości semantycznej.

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

Strumień danych: etap 03 — typed Parquet, polityka artefaktów i immutable
exporter w RetailOps, następnie importer i curated w AI. Lokalny audyt 02
w repo RetailOps potwierdza gotowość źródła; [audyt 11](evidence/11-audit.md)
zapisuje odczytany stan i granicę tej weryfikacji.
Drugi strumień na `ai/rag-corpus`: PR obecnego zakresu offline, ścieżka
rzeczywistego providera oraz użytkowa kwalifikacja/aktywacja etapu 11.
Zgody na obecny korpus/etykiety zapisano; odbiór jakości pozostaje otwarty.
Real embeddings/Bedrock smoke są w 12. Adaptery i test doubles można rozwijać
wcześniej, ale pełny agent wymaga ukończenia 10 i 11.
RAG i dane mogą postępować równolegle w osobnych branchach/worktree.

Token metryk nie jest systemem tożsamości użytkowników. MLflow ma lokalną
izolację sieciową, bez aplikacyjnego auth. Pipeline danych, modele, wyszukiwanie
użytkowe RAG, agent, streaming i cloud mają własne późniejsze bramki.
