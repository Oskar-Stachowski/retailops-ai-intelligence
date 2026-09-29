# Odbiór trzeciego zakresu AI 12

**2026-09-29 · ograniczony LangGraph i kanoniczne dowody.** Branch `ai/12-tools`,
po zakresie chat `ca5618e`. [Instrukcja](../agent-graph.md),
[bieżące bramki](../STATUS.md), [zapis kontroli](12-graph.json).

Graf ma zamknięte ścieżki, jedno dogranie brakującego dowodu i jedną naprawę
w pozostałym budżecie. Serwer wyznacza dokładne wywołania z typed requestu,
tworzy fakty z wyników i sprawdza całe twierdzenie, nie sam source_ref.
Różnica sprzedaży zachowuje zgodny grain/jednostkę, dwa okresy, refs i wzór;
literalne cytaty nie pozwalają swobodnie stwierdzić wdrożenia.

## Kontrole

Pełna regresja: **879 passed**, 126,93 s. Zawiera **60 nowych przypadków grafu**
oraz łącznie **236 przypadków agent tools/chat/graph**. Ruff/format (200 plików),
strict Mypy (118), kontrakty, docs, wheel/sdist, Compose config i Gitleaks
przechodzą. Wheel poza źródłami odtwarza config-check i pełny fake przebieg:
1 tool call, 2 model calls, zweryfikowany wynik i trace, bez AWS.
Config ID i checksum kodu/locka podaje [zapis kontroli](12-graph.json).
Testy obejmują pełny przebieg dla
intencji biznesowych, porównania i cytowanej wiedzy oraz negatywne przypadki:
nieprawdziwa liczba/jednostka/grain/okres/wniosek/summary, przyczynowość,
niezatwierdzona akcja, auth/scope, brak danych, unavailable, stale, konflikty,
mapping, retry/repair, deadline/budget/cancellation, TTL/capacity/owner trace,
brak checkpointów/remote tracing i acykliczność rzeczywistego grafu.

## Granice

Profile są jawnie fake/test; nie wykonano AWS chat, nowej ewaluacji embeddings,
DB/outbox ani zapisów RetailOps. Backend wiedzy w nowych testach ma syntetyczne
wektory i pinned spy. Typed fixtures nie potwierdzają rzeczywistych źródeł ML.
Nie mierzymy jeszcze doboru narzędzi/groundedness swobodnego LLM na golden set.
Kanoniczna walidacja jest celowo ograniczona do zamkniętych intentów, literalnych
obserwacji/cytatów i jednego wzoru; nie jest ogólnym testem rozumienia języka.

Trace jest ograniczony i autoryzowany, ale w pamięci; restart go usuwa.
Otwarte: polityka sugestii, golden odpowiedzi, Assistant API, trwały trace,
admission, adapter/circuit breaker/real chat smoke i źródła oraz E2E z AI 10.
Nie wykonano zdalnego CI ani publikacji tego zakresu na main.
