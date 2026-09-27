# Aktualny status

**2026-09-27 · in_progress · fundament etapu 01.**

Repo udostępnia importowalny pakiet, komendy `version` i `config-check`,
bezpieczne błędy konfiguracji, kontrakt metadanych CLI i lokalne kontrole
lint/type/test/docs/package/secrets. [Evidence](evidence/01-foundation.md)
opisuje wykonane próby. Workflow GitHub jest przygotowany; repo jest lokalne,
bez remote, zdalnego przebiegu CI i skonfigurowanej ochrony gałęzi.

## Następna praca

1. Rozdzielić role API/domeny/adapters/pipelines wraz z pierwszymi rzeczywistymi
   komponentami; uruchomić health/ready/version/metrics, correlation i error contract.
2. Dodać persistence AI i oddzielny MLflow, jawne migracje, Compose,
   testy awarii DB oraz restartu zachowującego dane.
3. Rozszerzyć wykonywalne kontrakty o wersje danych/runów; od początku
   chronić własne endpointy administracyjne i API.
4. Po podłączeniu repo do GitHub uruchomić Required CI i skonfigurować ochronę
   głównej gałęzi, wymagając `required-result`.

Serwer, DB, MLflow, pipeline danych, modele, RAG, agent i cloud deploy są
planowane. Cały etap 01 pozostaje otwarty. Odbiór 01 poprzedza odbiór źródła
RetailOps w 02; nie uruchamiamy treningu na danych niegotowych po audycie 00.
