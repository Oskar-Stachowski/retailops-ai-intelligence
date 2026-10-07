# AI 08 — rzeczywista końcowa jakość zatwierdzonej kampanii

Kampania v2 przeszła na wszystkich sześciu przygotowanych źródłach. To **9296
ocenialnych punktów fizycznych**; powtarzające się klucze i nakładające się okna
nie są traktowane jako niezależne epizody ani łączone w jeden wskaźnik jakości.
Model LR z upstream, sigmoid C10, progi 25/50/90% i kolejka top50% pozostają
zamrożone. Nie wykonano ponownego treningu, kalibracji ani wyboru na final TEST.

| Świat | Seed | Punkty | AP | Brier | ECE | Recall top50 | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Późniejsze obciążenia | 42 | 2627 | 0.9846 | 0.0549 | 3.29% | 92.51% | 131 | 97 |
| Późniejsze obciążenia | 137 | 2609 | 0.9859 | 0.0494 | 1.98% | 92.02% | 121 | 104 |
| Późniejsze obciążenia | 2026 | 2628 | 0.9904 | 0.0415 | 2.71% | 95.43% | 119 | 58 |
| Zgodny zakres | 42 | 477 | 0.9881 | 0.0480 | 3.57% | 94.71% | 27 | 12 |
| Zgodny zakres | 137 | 473 | 0.9905 | 0.0358 | 2.51% | 95.61% | 22 | 10 |
| Zgodny zakres | 2026 | 482 | 0.9993 | 0.0130 | 2.04% | 98.28% | 15 | 4 |

**90 kontroli zaliczonych, 3 jawne ostrzeżenia, 0 blokad.** Ostrzeżenia są wyłącznie
w małych kategoriach świata matching: seed42, n67, ECE15.079%; seed42, n65,
ECE15.052%; seed137, n53, ECE17.334%. Ich AP, Brier i minimum obu klas przechodzą.
Ścisłe sprawdzenie ECE nadal ma `false`; prospektywnie zaakceptowana polityka
v2 pozwala zapisać `warning`, a nie ukryć wyniku jako ścisłe zaliczenie.
Globalne, lokalizacyjne, inventory-constrained i wymagane scenariusze przechodzą
bez odstępstw. Lista obejmuje 72 kontrole segmentów i 21 scenariuszy/coverage.

## Wykonanie i trwałe dowody

[Rzeczywisty run 37273978383](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37273978383)
wykonał sześć równoległych par native/wheel na commicie
`048f7b311b82dbf831718565289a54166cbe7d36`. Wszystkie sześć jobs evaluate przeszło.
Każda para dała identyczne publiczne bajty wyników. Brak refitów i generowania
nowych źródeł. Najdłuższa para trwała 18.11min; suma par to 92.36min.
Maksymalny RSS wyniósł 326.46MiB, logiczny scratch 435.23MiB; najmniejszy zapas
dysku runnerów 84.37GiB. Lokalnej kampanii/Dockera nie uruchamiano.

Zachowane i sprawdzone względem digest artefaktu:

- [Sześć raportów jakości](08-25-final-quality.json).
- [Dowody wykonania i pomiarów](08-25-execution-evidence.json).
- [Weryfikacja pobrania](08-25-download-verification.json).

Publiczny cold verifier powtórzył wszystkie równania bramek i autoryzację v2.
ID jakości: `stockout-final-quality-sha256-92320157b0a6e66aab6784104af1b936a5c1cd67860ac4ec3e4abb9ceddbc1de`.
ID wykonania: `stockout-final-execution-sha256-c9e5a27bb2e9b79bed00efbec7078708c90bae8940a424ca2a480e3199f217a0`.

## Osobna kwalifikacja do użycia

Collector jakości przeszedł. Późniejszy qualifier wykrył adapter wejść oczekujący
upstream2.0 zamiast faktycznego, zamrożonego upstream2.1. Dlatego całego runu
nie oznaczamy jako sukces. Poprawka dodaje pełny replay obu formatów w osobnym
adapterze publicznym; frozen evaluator i jego digest pozostają niezmienione.
Recovery pobiera tylko siedem dokładnych artefaktów, weryfikuje ich SHA256 oraz
sukces wszystkich sześciu evaluate. Ponownie tworzy qualification, nie ocenia TEST.

Sama zaliczona jakość nie zamyka AI08. Osobny rzeczywisty test qualification,
review, image/MLflow, register/promote/rollback/reject, durable worker i API oraz
wymagane CI i merge/main pozostają warunkami końcowego odbioru.

Dane są syntetyczne. Wyniki nie potwierdzają skuteczności u rzeczywistego detalisty.
Koszt FP1/FN5 jest ilustracyjny; nie jest obietnicą oszczędności pieniężnych.
