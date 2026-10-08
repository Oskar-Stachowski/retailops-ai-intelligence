# Ograniczony graf i sprawdzalne dowody AI 12

Zakres działa bez AI 10: LangGraph wykonuje zamknięty przebieg z typed fixtures
oraz serwerowym adapterem wiedzy AI 11. [Status](STATUS.md),
[odbiór](evidence/12-graph.md), [narzędzia](agent-tools.md),
[budżet rozmowy](agent-chat.md). Jest to lokalny interfejs domenowy;
[Assistant HTTP API](assistant-api.md) ma osobny odbiór; rzeczywiste źródła
biznesowe pozostają do podłączenia.

## Przebieg i kontrola offline

```bash
uv run --locked retailops-ai agent-graph-check agent/graph.fake.prepaid.v3.json
make contracts-check
make agent-security-test
```

CLI sprawdza konfigurację, kod/schematy, prompty i wersję LangGraph bez
wykonania providera/narzędzi. [Manifest](../agent/graph.fake.prepaid.v3.json) wiąże
`bounded-langgraph-v1`, politykę `typed-facts-v2`, kod granic wykonania,
schematy, budżet, chat model, prompty v4 i przestrzeń wiedzy AI 11.
Zmiana powiązanego składnika zmienia config ID i wymaga ponownej kontroli.
`make contracts` nie przepisuje zatwierdzonych checksum manifestu.

Ścieżka: validate/auth → plan → tools/retrieve → check evidence → synthesize →
validate output → persist trace. Brakująca, jeszcze niepróbowana informacja
dopuszcza jedną dodatkową ścieżkę plan/tools/check. Jedna naprawa w całym runie
dotyczy planu albo odpowiedzi. Graf jest acykliczny, z limitem 20 kroków;
deadline 45 s i budżety poprzedniego zakresu nie resetują się.

`GraphRunner.run_json` przyjmuje poświadczenie oddzielnie od
[GraphRequest](../contracts/agent/v1/graph-request.v1.schema.json). Request ma
question, zamknięty intent, scope, as-of, okres, opcjonalny okres porównawczy
i limit. Principal/role, trace/index, URL/SQL i conversation nie są polami
requestu. Auth/scope i wymagane capabilities są sprawdzane przed modelem.
Serwer wylicza dokładne dopuszczalne wywołania. Model wybiera ich podzbiór,
ale nie zmienia produktu, dat, okresu ani źródła i nie powtarza próbowanego call.
Wiersze: domyślnie 5, maksymalnie 20. Katalog: do 40 faktów, odpowiedź: do 5.

Intenty: sales, sales_comparison, inventory, forecast, risk, anomalies,
operations, model, documentation, verified_state, investigation, recommendations, refuse.
To kontrolowane wejście, nie klasyfikator dowolnego pytania naturalnego.
Jawne polecenia zapisu, kodu i ujawnienia sekretu mają dodatkową zachowawczą
regułę odmowy. Katalog i uprawnienia nie dopuszczają takich narzędzi niezależnie
od rozpoznania słów w pytaniu.

## Liczby i znaczenie

Serwer tworzy kanoniczne fakty z uprawnionych wyników: wartość, jednostka,
grain, okres/as-of i ref; forecast także origin, target date, model i release.
Model wybiera fakty. Summary jest dokładnym połączeniem ich twierdzeń;
confidence, outcome, citations i limitations też pochodzą z polityki.
Poprawny ref ze zmienioną liczbą, jednostką, okresem, produktem, znaczeniem
lub swobodnym podsumowaniem nie przechodzi walidacji.

Wzór `sales-period-difference-v1` oblicza aktualny okres minus poprzedni.
Okresy są rozłączne i równej długości; produkt, lokalizacja, kanał i jednostka
identyczne. Claim zachowuje dwa okresy, refs i source as-of. Decimal operuje na
reprezentacji wartości typed float z precyzją obejmującą ich skończony zakres:
`0.3 - 0.1 = 0.2`, bez artefaktu arytmetyki binarnej. Nie ma eval/Python LLM.

Dokument wspiera literalny cytat dopiero po sprawdzeniu
[reguły pytania i kompletności dowodów](agent-document-evidence.md).
Reguła serwera wiąże wymagane informacje z konkretnymi fragmentami źródeł.
Każde wymaganie musi być pokryte w pobranych oraz w wybranych faktach.
Brak reguły lub wymaganej informacji daje `insufficient_evidence`.
Cytat ma do 800 znaków, status, fact scope i rewizję; nie jest automatycznie
pierwszymi 240 znakami dokumentu.
Specified pozostaje planem, historical/deprecated odniesieniem historycznym.
Verified_state dopuszcza tylko pobraną wiedzę verified. Model nie dopisze do
cytatu „EKS wdrożono”. Wersja wdrożonego modelu pochodzi z typed model status;
alias lub pojedynczy forecast nie zastępują dowodu wdrożenia/approval.

Profil potwierdza wąskie znaczenie obserwacji i literalnych cytatów. Nie
rozstrzyga trafności dowolnej parafrazy, kompletności odpowiedzi na dowolne
pytanie ani przyczynowości. Retrieval zachowuje bramki AI 11; jakość przyszłych
swobodnych odpowiedzi wymaga golden set.

## Braki i sprzeczności

- No_data daje insufficient_evidence, nigdy zmierzone zero. Wymagane źródło
  unavailable daje `dependency_unavailable` bez pozornej odpowiedzi.
- Stale inventory, nieobsługiwany grain i brak wyników mają limitation.
  Odrzucony wynik nie ujawnia timestampu; freshness ma missing.
- Różne wartości tej samej prognozy i niezaliczony quality gate blokują answered.
  Agent nie wybiera samodzielnie „prawdziwego” źródła.
- Wiele stock locations dla selling grain wymaga zatwierdzonej agregacji.
- Observed sales nie dowodzą uncensored demand; cross-signal observations
  nie ustalają przyczyny. Odpowiednie limitations są obowiązkowe.

Modelowe sugestie i replenishment quantities są odrzucane.
[Deterministyczna polityka](agent-evaluation.md) tworzy wyłącznie kandydatów
do przeglądu przez człowieka; draft action musi dokładnie odpowiadać regule
i wybranym dowodom tego samego grain. Trwały producent/outbox/E2E pozostają
otwarte. Wersjonowany golden sprawdza ten kanoniczny profil na fixtures.

## Bezpieczny ślad

Końcowy węzeł zapisuje [SafeTrace](../contracts/agent/v1/safe-trace.v1.schema.json)
w `MemoryTraces`: owner/scope, IDs/config/checksum requestu, node status/latency,
refs, tokeny/koszt i liczniki. Brak question, odpowiedzi, tool payloadów,
sekretów i toku rozumowania. Refs mają limit 100 z jawnym total. Czas samego
zapisu trace nie jest osobnym pomiarem; w metadanych tego węzła wynosi 0 ms.

Odczyt wymaga właściciela, operator, assistant:query i dozwolonego scope.
Retencja: najwyżej 900 s; capacity: najwyżej 100. Pełny store daje
`trace_unavailable`. Timeout/anulowanie zachowują ślad po uwierzytelnieniu.

Brak checkpointów i pamięci konwersacji. Stan routingu nie zawiera prywatnych
danych; request/wyniki pozostają w kontekście danego runa. Remote tracing jest
wyłączony także przy zmiennych LangSmith. Restart usuwa ślady z pamięci.
[Assistant API](assistant-api.md) zapisuje końcowy ślad, odpowiedź i kandydatów
w PostgreSQL; ma osobny odbiór persistence/admission.
