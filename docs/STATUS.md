# Aktualny status

**2026-09-28 · in_progress · persistence i lokalny stos etapu 01.**

Pakiet, typed settings, CLI i diagnostyczny HTTP działają razem z izolowanym
PostgreSQL AI, pgvector oraz MLflow na oddzielnej bazie i roli.
[Instrukcja stosu](local-stack.md) obejmuje pierwsze uruchomienie, jawne migracje,
wolumeny, granice sieciowe i bezpieczny shutdown.
[HTTP](http-service.md) ma rolę foundation bez DB albo ai_api z rzeczywistą sondą
schematu i bazy. Nie ma jeszcze endpointów predykcji.

[Dowody persistence](evidence/01-persistence.md) opisują rzeczywisty lokalny Compose,
restart/awarię/recovery i trwałość danych; osobno wskazują testy adaptera z fake.
[HTTP](evidence/01-http.md) i [fundament](evidence/01-foundation.md) zachowują zakres
wcześniejszych pomiarów. Wymagane kontrole obejmują kod, migracje, Compose i runtime
persistence. Repo nadal jest lokalne: brak remote, przebiegu zdalnego CI i ochrony gałęzi.

## Następna praca

1. Wykonywalne wersje kontraktów dataset/feature/label/prediction/run/tool
   i reguły kompatybilności; uprawnienia pierwszych endpointów aplikacyjnych.
2. Po publikacji repo wykonać Required CI i ustawić ochronę głównej gałęzi
   wymagającą required-result.

Pipeline danych, modele, RAG, agent i cloud pozostają planowane.
Cały etap 01 pozostaje otwarty. Odbiór 01 poprzedza odbiór źródła RetailOps w 02.
