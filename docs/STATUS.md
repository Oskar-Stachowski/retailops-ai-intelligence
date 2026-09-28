# Aktualny status

**2026-09-28 · fundament etapu 01 odebrany lokalnie i w zdalnym Required CI.**

**Etap 11 w realizacji:** [korpus wiedzy](knowledge-corpus.md) ma rejestr
kandydacki 20 dokumentów z obu repo, przypięte SHA/checksums, klasy dostępu,
statusy i kontrolę dowodów. Techniczna walidacja offline nie oznacza akceptacji
redakcyjnej ani aktywnego RAG.
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
pozostaje propozycją i nie ma aktywnego retrieval.
[Odbiór lifecycle](evidence/11-lifecycle.md): 463 testy, czysty checkout,
konkurencyjna aktywacja, rollback i zachowany pełny pin po restartach PostgreSQL.
[Retrieval i golden set](knowledge-retrieval.md) dodają exact cosine pgvector,
deterministyczną selekcję, bounded context, grant knowledge:read, filtry
repo/type/status/access, live deny dla starych pinów i 36 wersjonowanych pytań.
Ścieżka runtime działa na kwalifikowanych indeksach testowych; rzeczywisty
korpus i etykiety pozostają propozycją. Fake report nie otwiera aktywacji.
[Odbiór retrieval](evidence/11-retrieval.md): 498 testów, świeży PG/HTTP,
7/7 krytycznych przypadków oraz jawnie nieprzechodzące progi Recall@5/MRR z fake.

[Administracja indeksami](knowledge-administration.md) dodaje osobny grant,
trwałe runy/idempotencję, odczyt current i worker zatwierdzonych snapshotów
`test/fake`. Run zapisuje kandydata i raport; nie aktywuje indeksu.
Użytkowe profile z golden evaluation wymagają rozszerzenia i odbioru jakości.

**Postęp etapu 11: około 85%** — szacunek zakresu implementacji, z uwzględnieniem
niezamkniętych odbiorów. Rejestr/metadata, parser, fake/storage, test lifecycle
i ograniczony retrieval są zaimplementowane. Punkt 7 ma golden set i raport,
administracyjne runy mają odbiór techniczny w zakresie test/fake.
Odbiór jakości/źródeł i profil użytkowy pozostają otwarte.

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

Główny kierunek: DATA-01 etapu 02 w RetailOps — konfiguracja, identity i manifest v2.
Drugi strumień na `ai/rag-corpus`: near duplicates, odświeżenie/przegląd źródeł
i zamknięcie kwalifikacji etapu 11. Kandydat i golden labels
wymagają przeglądu/odświeżenia źródeł, doboru statusów/access i odbioru jakości
przed użytkową aktywacją. Real embeddings/Bedrock smoke i agent są w etapie 12.
Praca nie zależy od zakończenia DATA-01. Etap 03 wymaga odbioru
źródła z 02, a forecasting i serving kolejnych bramek.

Token metryk nie jest systemem tożsamości użytkowników. MLflow ma lokalną
izolację sieciową, bez aplikacyjnego auth. Pipeline danych, modele, wyszukiwanie
użytkowe RAG, agent, streaming i cloud mają własne późniejsze bramki.
