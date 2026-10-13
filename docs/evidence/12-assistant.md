# AI 12 — Assistant API, persistence i admission

**2026-09-29 · lokalny odbiór na `ai/12-tools`.**
[Instrukcja](../assistant-api.md), [aktualny status](../STATUS.md),
[manifest odbioru](12-assistant.json), [Compose](12-assistant-compose.json),
[golden](12-assistant-golden.json). Zakres nie wymaga AI 10.

## Wykonane zachowanie

- Exact MVP POST queries i GET safe runs, UUID, correlation/problem-details,
  lokalne bearer/role/scope, prawa narzędzi i osobne assistant:audit.
- Odczyt właściciela ukrywa trace po cofnięciu praw do scope, narzędzi lub
  wiedzy. Cudzy i nieistniejący trace dają jednakowe 404.
- Migracja `0009_assistant`: running przed grafem; odpowiedź, bezpieczny trace
  i pełny review candidate są utrwalane jedną transakcją w bazie AI.
- Wspólne PostgreSQL admission: max 2 równoległe runy principal, token/cost
  reservations bez zwrotu w oknie, utrwalone wiązanie polityki, crash expiry
  i fencing completion. Retencja/capacity ograniczają własny store.

## Pomiar

Pełna regresja: **944 testy**, w tym 33 nowe testy Assistant. Po oddzieleniu
importu LangGraph od startu diagnostyki: dodatkowe **144 testy HTTP/access/
process/persistence/Assistant**. Wszystkie przechodzą. Ruff: 224 pliki;
Mypy strict: 129 plików. Kontrakty, wheel/sdist i Compose config przechodzą.
Gitleaks Git: 60 commits, directory — bez znalezisk.

Wersjonowany golden po zmianie kodu/schema/release, bez zmiany 50 oracles:
**50/50 przypadków, 36/36 krytycznych**, numeric 40/40, grounded 46/46,
cytaty 6/6, refusal 4/4, zero zbędnych calls, p95 **840,91 ms**, koszt fake 0 USD.
`make agent-evaluate PROVIDER=fake` przechodzi. Wheel poza drzewem źródeł
otwiera Assistant OpenAPI bez LangGraph i wykonuje pełne 50 przypadków z tym
samym release/checksum. Zapisane usage są syntetyczne, nie rachunkiem Bedrock.

Rzeczywisty Compose/PostgreSQL/HTTP:

- Trzy osobne procesy próbują admission tego samego principal: dwa accepted,
  trzeci 429. Odczyt z innego store widzi running; foreign/scope revocation ukrywa run.
- Nieaktualny claim/completion jest odrzucany. Wygaśnięcie lease po przerwaniu
  pracy daje failed/deadline_exceeded. Odmienna polityka procesu daje 503.
- Token/cost debit pozostaje po nieudanym runie i blokuje następne admission.
- HTTP zapisuje answer/trace/recommendation; SIGKILL procesu API i ponowny
  start zwracają identyczny bezpieczny trace.
- Wstrzyknięty błąd SQL przy zapisie sugestii daje bezpieczne 503; transakcja
  nie zostawia dodatkowej odpowiedzi ani częściowej sugestii.
- Pełny smoke zachowuje trzy związane wyniki po SIGKILL bazy/API i down/up,
  sprawdza awarię DB, health/readiness, istniejące bramki RAG/MLflow i brak
  sekretów w logach. Na końcu zatrzymuje wyłącznie izolowany stos AI 12.

Starszy skrypt próbował cold startup przez 4 s. Zmierzony import/składanie
serwera w kontenerze trwał 10,42 s; helpery czekają teraz do 20 s. Limity
wykonania query/provider/tool i progi jakości pozostały bez zmian. LangGraph
jest importowany dopiero przez runtime grafu; diagnostyka go nie uruchamia.

## Granice odbioru

HTTP/PG smoke używa jawnego scripted backendu w APP_ENV=test. Unit tests
wykonują rzeczywisty graf z fixture tools/chat; golden ma niezależne oracles.
Nie wykonano AWS chat ani ponownego pomiaru embeddings/retrieval jakości AI 11.

Standardowe serve nie ma jeszcze realnego chat/planner/source resolvera i
zweryfikowanych danych biznesowych; query jest wtedy 503. Nie ma historii
konwersacji, odczytu recommendations ML, outbox/v2, UI ani operational writer.
Utrwalony kandydat nadal wymaga oceny człowieka i kontroli expiry; zapis nie
zatwierdza jego reguły ani nie inicjuje pracy RetailOps.

AI 12 pozostaje otwarty. Ten commit jest lokalny; zdalny Required CI i publikacja
nie są częścią odbioru. Bieżące dalsze bramki są wyłącznie w [statusie](../STATUS.md).
