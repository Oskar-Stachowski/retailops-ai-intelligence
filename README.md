# RetailOps AI Intelligence

**Status: fundament etapu 01 odebrany.** Dalszy rozwój AI jest w realizacji.
Obecny zakres: pakiet Python, konfiguracja, CLI, lokalny serwis diagnostyczny HTTP,
PostgreSQL/pgvector, oddzielny MLflow, migracje, Compose, wykonywalne kontrakty
danych/run/tool, lokalne poświadczenia i scope API oraz kontrole CI.
[Etap 11](docs/knowledge-corpus.md) w realizacji: rejestr kandydackiego korpusu,
walidacja Git, [parser/chunker](docs/knowledge-chunks.md) oraz
[fake embeddings i zapis kandydata pgvector](docs/knowledge-index.md) oraz
[atomowe przełączanie indeksów testowych](docs/knowledge-lifecycle.md).
[Retrieval, filtry uprawnień/statusów i golden set](docs/knowledge-retrieval.md)
mają wykonywalną ścieżkę offline/test i 44 pytania ewaluacyjne.
[Administracyjne runy indeksowania](docs/knowledge-administration.md) mają
trwałe stany, idempotencję i worker dla zatwierdzonych snapshotów testowych.
[Raport podobnych treści](docs/knowledge-review.md) wskazuje dokładne i near
powtórzenia do przeglądu, zachowując metadata oraz cytaty.
[Odświeżone źródła i etykiety](docs/knowledge-sources.md) wiążą 29 dokumentów
z konkretnymi SHA; akceptacja treści i jakości pozostaje oddzielną bramką.
Wyszukiwanie i aktywacja rzeczywistego korpusu wymagają kolejnego odbioru jakości.
[Uprawnienia API](docs/access-control.md).
[Kontrakty i walidacja offline](docs/data-contracts.md).
[Uruchomienie stosu](docs/local-stack.md) i [HTTP](docs/http-service.md).

- [Dokumentacja i uruchomienie](docs/README.md)
- [Aktualny status i następna praca](docs/STATUS.md)
- [Decyzje architektoniczne](docs/architecture/decisions.md)
- [Zasady zmian](docs/contributing.md) i [bezpieczeństwo](docs/security.md)
- [Licencja MIT](LICENSE)

Źródło planu: [RetailOps](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/tree/cbf28b2/docs/plans/ai).
Generator, frontend i baza operacyjna należą do RetailOps. To repo ma własny lifecycle.
