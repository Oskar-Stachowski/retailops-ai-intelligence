# Sugestie i ewaluacja odpowiedzi AI 12

Ten zakres działa lokalnie bez AI 10. Graf kwalifikuje kandydatów do przeglądu
przez człowieka i przechodzi wersjonowany zestaw 50 przypadków. Nie publikuje
sugestii do RetailOps. [Status](STATUS.md), [graf](agent-graph.md),
[odbiór](evidence/12-evaluation.md).

## Reguły sugestii

Polityka `read-only-review-v1` jest częścią konfiguracji grafu. Model nie
wybiera progów ani priorytetu. Sugestia wymaga kompletnych, spójnych dowodów;
brak danych, stale inventory, konflikt prognoz lub niejednoznaczny mapping
blokują kandydatów. Brak kwalifikującego sygnału oznacza pustą listę sugestii,
bez twierdzenia, że ryzyko nie istnieje.

| Kandydat | Warunek i zakres |
|---|---|
| `review_replenishment` | Probability ≥ max(0,8, próg źródła), jawna kalibracja, jeden świeży zapas z zgodnym source ref/as-of, zatwierdzony wdrożony model **ryzyka** o zgodnym model/release ID oraz pełna prognoza sprzedaży na każdy dzień horyzontu z passed quality i jednym release/model. Priorytet high. |
| `investigate_anomaly` | Typed detector podaje różne observed/expected values. Priorytet medium; proponujemy przegląd obserwacji i danych, bez ustalania przyczyny. |
| `refresh_source_data` | Typed operations ma degraded/stopped lub lag ≥ 60 s. Priorytet medium, przy stopped high; człowiek sprawdza świeżość i read model. |

Intencja `recommendations` jest nowym, zamkniętym wejściem
[GraphRequest](../contracts/agent/v1/graph-request.v1.schema.json). Wymaga czterech
uprawnionych odczytów: risk, inventory, forecast, model status. Horizon ma 1–14
przyszłych dni. Limit wierszy może uniemożliwić zebranie pełnej prognozy dla
dłuższego horyzontu; wówczas kandydat nie powstaje. Prognoza passed nie jest
osobnym dowodem jej wdrożenia, a observed sales nie identyfikują uncensored demand.
Reguła nie wylicza replenishment quantity ani nie uzasadnia konkretnej ilości.

Kandydat zawiera grain, ewentualną stock location, action/priority/rationale,
refs, model releases, wersję i hash polityki, source as-of, expiry oraz
`requires_human_review=true`, `status=proposed`. Identity wynika z tych danych;
retry z identycznymi dowodami/polityką daje tę samą identity. Expiry to najstarszy
source as-of + najwyżej 300 s, dodatkowo zaostrzone freshness policy narzędzi.
Reguła sprawdza zegar serwera; historyczny snapshot nie daje aktualnej sugestii.
Zmiana źródła/release/polityki wymaga ponownej kwalifikacji.

Do pięciu kandydatów jest sortowanych według priorytetu, typu, produktu,
lokalizacji i identity. Odpowiedź może zawierać tylko kandydatów, których refs
mają wybrane kanoniczne fakty dla tego samego grain. Wszystkie pola draft action
muszą być dokładną kopią reguły. Podmiana, pominięcie wymaganej akcji, duplikat
lub dopisanie ilości kończy się kontrolowaną naprawą/błędem.

[SuggestionCandidate](../contracts/agent/v1/suggestion-candidate.v1.schema.json)
jest lokalnym kandydatem. [Assistant API](assistant-api.md) dodaje
answer/trace/config binding i trwały zapis w AI. Outbox pozostaje AI 10. Obecne GraphResult
nie oznacza, że sugestia jest utrwalona, zaakceptowana lub widoczna w UI.
Nie ma uprawnienia do wykonania zamówienia, odświeżenia źródła ani workflow.

## Kontrola offline

```bash
make agent-evaluate PROVIDER=fake
make agent-security-test
make contracts-check
```

`make check` zawiera również `agent-evaluate`. Dla nowego pełnego raportu:

```bash
uv run --locked retailops-ai agent-evaluate --provider fake \
  --config agent/graph.evaluate.fake.native-v12.v1.json \
  --golden agent/golden.canonical.v1.json \
  --release agent/evaluation-release.fake.native-v12.v1.json \
  --rag-golden knowledge/golden.semantic.v1.json --lock uv.lock \
  --output /tmp/retailops-agent-evaluation.json
```

Output musi być nowym plikiem; CLI nie nadpisuje raportu. Exit 0 oznacza wszystkie
bramki passed, 1 failed gates (pełny raport pozostaje dostępny), 2 błąd wiązań,
wejścia lub zapisu. Raport zawiera wyłącznie IDs, liczniki, latency, syntetyczne
tokeny/koszt i bezpieczne nazwy niezaliczonych kontroli, bez pytań/odpowiedzi,
tool payloadów, poświadczeń i toku rozumowania.

## Zestaw i wiązania

[Golden](../agent/golden.canonical.v1.json) ma 50 przypadków: biznesowe odczyty,
porównanie okresów, reguły sugestii, cytaty/statusy, missing/stale/conflict/mapping,
auth/scope, injection, odmowy, fałszywe liczby/znaczenie/cytaty/akcje i bounded
extra/repair/provider failure. Sześć pytań pochodzi bez zmian z AI 11; reszta
jest nowa i dotyczy typed tools lub granic grafu. Hash źródłowego golden i
powiązanie question/case ID są sprawdzane przed wykonaniem.

Aktualna wersja znaczenia etykiet to `agent-canonical-golden-v2`
(format pliku/kontraktu pozostaje v1). [Przegląd dokumentów](evidence/12-document-label-review.json)
wiąże zmianę z poprzednią wersją, pytaniami i źródłami użytymi przy autorstwie.
Pięć przypadków ma wystarczające dowody; szósty zachowuje nietrafny dokument
`verified` i wymaga `insufficient_evidence`. Pytanie health/ready wymaga
obu informacji, a nie jednego przypadkowego cytatu. Pozostałe 42 przypadki
biznesowe i bezpieczeństwa pozostają niezmienione; dwa negatywne testy
dokumentacji otrzymały spójne pytania/źródła. Progów nie obniżono.

Typed outputs, scripted replies i oczekiwane odpowiedzi są zapisanymi fixtures.
Fake nie odczytuje katalogu serwera w celu konstruowania odpowiedzi. Oracles
zostały opisane niezależnie od runtime EvidencePolicy; polecenia testowe i
generatory schemas ich nie przepisują. Test celowo zmienionego oracle potwierdza,
że ewaluacja zwraca failed. Etykiety mają stan `proposed`; brak deklaracji
niezależnego przeglądu człowieka lub akceptacji jakości modelu.

Dokumenty i pin w tym zestawie są **syntetycznymi metadanymi**. Nazwy statusów
sprawdzają mechanikę cytowania/statusów, a pin przechodzi ścisły kontrakt w
środowisku test. Nie aktywujemy go w bazie i nie mierzymy embeddings, rankingu
ani rzeczywistych sekcji AI 11. Osobny
[profil ewaluacji](../agent/graph.evaluate.fake.native-v12.v1.json) przypina ten fixture pin;
[profil grafu](../agent/graph.fake.native-v12.v1.json) nadal wskazuje użytkowy indeks AI 11.

[Evaluation release](../agent/evaluation-release.fake.native-v12.v1.json) wiąże config ID
grafu (kod, tool/response schemas, prompty v4, model, retrieval, index, budżety
i politykę), golden hash, kod Python całego pakietu (w tym ewaluator i jego
zależności aplikacyjne/schemas) oraz dependency lock. Hash nie
uwierzytelnia autora ani nie zastępuje zatwierdzenia. Zmiana powiązanego pliku
wymaga jawnej nowej konfiguracji/release i ponownego pomiaru. `make contracts`
sprawdza wiązania; nie uaktualnia ich ani golden labels automatycznie.

## Znaczenie metryk

Raport podaje numerator/denominator. Pusta metryka ma null, nie pozorne 100%.
W tym zamkniętym profilu wymagamy 100% deterministycznych kontroli i krytycznych
przypadków, zero zbędnych wywołań, p95 ≤ 5000 ms i koszt syntetyczny 0 USD.

- Tool selection/arguments porównuje faktyczne wywołania adapterów z etykietą;
  call budget dodatkowo sprawdza wszystkie próby, model calls, extra i repair.
- Schema pass dotyczy końcowego GraphResult, również poprawnego strukturalnie
  błędu/odmowy. Nie oznacza, że każdy błędny draft testowy był poprawnym JSON.
- Numeric faithfulness porównuje pełne kanoniczne business/calculation claims
  z oracle, łącznie z jednostką/grain/okresem/as-of. Groundedness obejmuje też
  document claims. To dokładne porównanie, bez LLM-as-judge/parafraz.
- Citation correctness porównuje pełne metadane cytatu; coverage wymaga cytatu
  dla document claim. Pominięcia, dodatki i duplikaty obciążają mianownik.
- Outcome, action policy, safety i refusal mierzą kontrolowane decyzje dla
  etykietowanych przypadków. Case pass wymaga wszystkich kontroli przypadku.
- Latency p95 jest nearest-rank z wall time grafu; tokeny są zapisanym usage
  fake i rezerwacją przy błędzie providera. Nie mierzą wydajności ani ceny Bedrock.

Gotowy [adapter Bedrock](agent-bedrock.md) oraz circuit breaker mają osobny
odbiór transportu oraz [wynik rzeczywistych prób](evidence/12-bedrock-real.md).
[Runtime dokumentacyjny](assistant-document-runtime.md) ma dwie trasy pytań,
resolver source AI 03 oraz realny RAG/chat. Kolejne kroki to rozszerzenie
plannera i adaptery źródeł biznesowych. Pełne AI 12 wymaga rzeczywistych
narzędzi, golden dla realnego modelu/retrieval oraz ścieżki sugestia → outbox/v2
→ RetailOps read API/UI z AI 10.
