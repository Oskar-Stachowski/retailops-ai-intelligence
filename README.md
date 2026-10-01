# RetailOps AI Intelligence

**Status: AI 04 — finalna v12, `ready` z zaakceptowanymi odstępstwami jakościowymi.**
[Decyzja i zakres odbioru](docs/evidence/04-v12-acceptance.md) obowiązują po
chronionym merge i Required CI. Etapy 01 i 11 są odebrane lokalnie.
Obecny zakres: pakiet Python, konfiguracja, CLI, lokalny serwis diagnostyczny HTTP,
PostgreSQL/pgvector, oddzielny MLflow, migracje, Compose, wykonywalne kontrakty
danych/run/tool, lokalne poświadczenia i scope API oraz kontrole CI.
[Etap 11 — semantyczny RAG](docs/knowledge-semantic.md) obejmuje zatwierdzony
korpus, wersjonowane fragmenty i rzeczywiste embeddings Bedrock, pgvector,
filtry uprawnień/statusów, 44 pytania golden, trwałe runy oraz kwalifikację,
atomową aktywację i rollback. Aktualny odbiór i granice opisuje
[status](docs/STATUS.md). Generowanie odpowiedzi i narzędzia agenta należą do AI 12.
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
