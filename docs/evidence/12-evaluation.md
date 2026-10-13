# Odbiór czwartego zakresu AI 12

**2026-09-29 · reguły sugestii i golden kanonicznych odpowiedzi.** Branch
`ai/12-tools`, po zakresie grafu `abc6103`.
[Instrukcja](../agent-evaluation.md), [bieżące bramki](../STATUS.md),
[zapis kontroli](12-evaluation.json), [raport golden](12-answer-golden.json).

Polityka tworzy trzy zamknięte typy kandydatów do przeglądu przez człowieka.
Przegląd replenishment wymaga czterech źródeł, zgodnego inventory/risk/model
lineage i pełnej prognozy passed. Kandydat ma stabilną identity, expiry,
policy hash i human review. Model nie zmienia tekstu, priorytetu, rationale
ani refs i nie wylicza ilości.

Golden ma 50 przypadków, 36 krytycznych, sześć pytań z AI 11 oraz zapisane
niezależne oracles. Konfiguracja, prompty v3, graf/polityka/schemas, zestaw,
cały kod Python pakietu i lock są związane wersjonowanym evaluation release.
Ewaluacja wykonuje rzeczywisty graf ze scripted fake oraz typed fixtures.

## Kontrole i pomiar

Pełna regresja: **911 passed**, 253,36 s, w tym **32 nowe przypadki pytest**
i łącznie **268 przypadków agent tools/chat/graph/suggestions/evaluation**.
Ruff/format (213 plików), strict Mypy (121), wszystkie kontrakty, docs,
wheel/sdist, Compose config i skan sekretów przechodzą.

`make agent-evaluate PROVIDER=fake` przechodzi. Gotowy wheel odtworzył poza
źródłami cały pomiar: **50/50 przypadków, 36/36 krytycznych**. Raport daje
numeric faithfulness **40/40**, groundedness **46/46**, citation correctness
oraz coverage **6/6**, refusal **4/4**, action policy, dobór narzędzi/argumentów,
output schema oraz call budget **50/50**. Zbędne wywołania: **0**.
P95 tego lokalnego wheel runa: **653,00 ms ≤ 5000 ms**. Usage/rezerwacje fake:
**8000 input / 1980 output tokens** łącznie, koszt syntetyczny **0 USD**.
Liczniki, przypadki, IDs i dokładne czasy podaje [raport](12-answer-golden.json).

Negatywne testy obejmują niekompletny forecast, duplikaty/release/quality,
przyszłą datę wygenerowania, wygasły snapshot, źle związany inventory/model,
zmieniony próg polityki, zmienioną/pominiętą/duplikowaną akcję i obcy grain.
Kontrole wiązań wykrywają zmianę golden, pytań AI 11, locka, kodu lub configu
przed providerem. Celowo fałszywy oracle daje failed, także numeric gate;
evaluator nie naprawia etykiety, aby wynik przeszedł.

## Granice

Odbiór jest lokalny i fixture only. Dokumenty/pin są syntetyczne; nie wykonano
nowego pomiaru rankingu/embeddings ani rzeczywistych sekcji AI 11. Brak AWS chat,
realnego model quality gate i business approval lokalnych progów. Etykiety
mają stan proposed; test wykrywania błędnego oracle nie zastępuje ich przeglądu.
Tokeny są syntetycznym usage i rezerwacjami budżetu; koszt 0 USD nie jest ceną AWS.

Trace pozostaje w pamięci, a kandydaci nie są persisted recommendations.
Brak outbox, publikacji v2 i projekcji/read API/UI RetailOps. Dalsze bramki
podaje wyłącznie [status](../STATUS.md). Pełne AI 12 nie jest zamknięte.
Nie wykonano zdalnego CI ani publikacji na main.
