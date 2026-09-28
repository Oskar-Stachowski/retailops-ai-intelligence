# Aktualny status

**2026-09-28 · in_progress · kontrakty danych i wykonania etapu 01.**

Pakiet, settings, CLI i diagnostyczny HTTP działają razem z izolowanym
PostgreSQL AI/pgvector i oddzielną bazą/rolą MLflow.
[Uruchomienie](local-stack.md), [HTTP](http-service.md).

[Kontrakty v1](data-contracts.md) są wykonywalne: dataset/feature/label/split/model/
prediction/run/tool/bundle, JSON Schema, syntetyczne fixtures i walidacja offline.
[Dowody](evidence/01-contracts.md) opisują testy struktury, PIT, lineage, identity,
stanów runa i bezpiecznych tool results. Nie ma jeszcze importera, treningu ani
endpointów predykcji. Schemat runa/tool nie oznacza działającego workera/agenta.

[Odbiór persistence](evidence/01-persistence.md) opisuje rzeczywisty Compose,
crash/restart, DB outage/recovery i zachowanie artefaktów.
[HTTP](evidence/01-http.md) i [fundament](evidence/01-foundation.md) zachowują zakres
wcześniejszych pomiarów. Required CI obejmuje kod, schemas, docs i persistence.
GitHub main ma required-result; CI bazowego 7d67530 ma success. Nowe commity
persistence i kontraktów są lokalne; ich zdalny CI czeka na push.

## Następna praca

1. Granica tożsamości i uprawnień pierwszych endpointów aplikacyjnych, z testami
   odmowy dostępu i ograniczenia scope, zanim udostępnimy dane biznesowe.
2. Po push odebrać Required CI nowych commitów, w tym job persistence.

Token metryk nie jest systemem tożsamości użytkowników. MLflow ma lokalną
izolację sieciową, bez aplikacyjnego auth. Cały etap 01 pozostaje otwarty.
Odbiór 01 poprzedza źródło RetailOps w 02. Pipeline danych, modele, RAG, agent,
streaming i cloud są planowane, z własnymi późniejszymi bramkami.
