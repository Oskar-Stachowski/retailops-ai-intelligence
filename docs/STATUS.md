# Aktualny status

**2026-09-27 · in_progress · bazowy HTTP w etapie 01.**

Działa pakiet, typed settings, CLI `version/config-check/serve` oraz lokalny
serwis `/health`, `/ready`, `/version` i chroniony tokenem `/metrics`.
[Instrukcja HTTP](http-service.md) opisuje gotowość roli foundation,
kontrole zależności, bezpieczne błędy, logi JSON i kontekst żądania.
Warstwy api/domain/pipelines/adapters mają rzeczywistą implementację.
Kontrakty OpenAPI i JSON Schema są sprawdzane razem z testami.

[Dowody HTTP](evidence/01-http.md) opisują wykonane kontrole, w tym proces
nasłuchujący na loopback. [Pierwszy fundament](evidence/01-foundation.md)
zachowuje zakres wcześniejszego odbioru. Podstawowe Required CI obejmuje
nowe pliki bez filtrów ścieżek. Repo nadal jest lokalne: brak remote,
zdalnego przebiegu CI i skonfigurowanej ochrony gałęzi.

## Następna praca

1. Dodać persistence AI i oddzielny MLflow: jawne migracje, Compose,
   rzeczywistą sondę DB, testy awarii i restartu zachowującego dane.
2. Rozszerzyć wykonywalne kontrakty o wersje danych/runów i dostosować
   uprawnienia do pierwszych endpointów aplikacyjnych.
3. Po publikacji repo uruchomić Required CI i skonfigurować ochronę
   głównej gałęzi, wymagając `required-result`.

Rola foundation nie ma zależności DB/modelowych; jej gotowość nie oznacza
gotowości predykcji. DB, MLflow, pipeline danych, modele, RAG, agent i cloud
deploy pozostają planowane. Cały etap 01 pozostaje otwarty. Odbiór 01
poprzedza odbiór źródła RetailOps w 02.
