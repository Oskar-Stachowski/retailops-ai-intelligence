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
endpointów predykcji. Schemat runa/tool nie oznacza działającego workera/agenta.

[Odbiór persistence](evidence/01-persistence.md) opisuje rzeczywisty Compose,
crash/restart, DB outage/recovery i zachowanie artefaktów.
[HTTP](evidence/01-http.md) i [fundament](evidence/01-foundation.md) zachowują zakres
wcześniejszych pomiarów. Required CI obejmuje kod, schemas, docs i persistence.
[Zdalny odbiór](evidence/01-remote-ci.md) potwierdza success Required CI po
push na main obu repozytoriów. W AI przechodzą checks, secrets, persistence
i required-result; main wymaga PR i aktualnej gałęzi również dla administratora.

## Następna praca

Główny kierunek: DATA-01 etapu 02 w RetailOps — konfiguracja, identity i manifest v2.
Drugi strumień na `ai/rag-corpus`: ograniczony retrieval, golden set i filtry
uprawnień/statusów etapu 11. Kandydat wymaga przeglądu/odświeżenia źródeł,
doboru statusów/access i odbioru jakości przed użytkową aktywacją.
Praca nie zależy od zakończenia DATA-01. Etap 03 wymaga odbioru
źródła z 02, a forecasting i serving kolejnych bramek.

Token metryk nie jest systemem tożsamości użytkowników. MLflow ma lokalną
izolację sieciową, bez aplikacyjnego auth. Pipeline danych, modele, wyszukiwanie
RAG, agent, streaming i cloud mają własne późniejsze bramki.
