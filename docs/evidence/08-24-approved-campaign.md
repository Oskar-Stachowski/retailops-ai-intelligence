# AI 08.24 — zatwierdzona polityka i kampania przed oceną końcową

Użytkownik: „zgadzam się z twoją rekomendacją. doprowadź AI 08 do ready”.
Zgoda obejmuje zachowanie modelu i sześciu źródeł, wybór 40%/50% na development,
jawne ostrzeżenia kalibracji małych kategorii i sześć równoległych ewaluacji.
Wersja v2 ma osobny manifest i zgodę; historyczna propozycja v1 nie była oceniana.

## Rzeczywiste porównanie development

[GitHub run 37273265180](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37273265180)
zakończył się sukcesem na b319b7dd4665c7287b072203aca8d515e2cc9fc9.
Replay trwał 264,88 s. Pobrano i zweryfikowano źródło matching 42, odtworzono
wszystkie rodzice i sprawdzono pełne features/upstream/label/temporal manifests.
Porównanie użyło wyłącznie zamrożonych development rows/outcomes, z kontrolą
liczebności oraz hashów trzech ról. Nie fitowano modelu, nie odczytano wektora
końcowych outcomes i nie zmieniono wcześniejszej kalibracji.

| Kolejka | Wybrane | TP | FP | FN | Recall | Precision | Koszt FP1/FN5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 20% (referencja) | 97 | 97 | 0 | 121 | 44,50% | 100,00% | 605 |
| 40% | 191 | 180 | 11 | 38 | 82,57% | 94,24% | 201 |
| 50% (wybrane) | 235 | 202 | 33 | 16 | 92,66% | 85,96% | 113 |

466 punktów obejmuje 218 zdarzeń. Wyboru dokonano przez najmniejszy koszt
wśród 40% i 50%, potem mniejszą pracę operatora i mniejszy procent przy remisie.
Koszty są umowne, nie stanowią oszczędności w złotych. Próg prawdopodobieństwa
50% pozostaje osobną diagnostyką i nie jest kolejką top 50%.

Pełne [porównanie](08-24-capacity-comparison.json), [wykonanie](08-24-capacity-execution.json)
i [weryfikacja pobrania](08-24-capacity-download-verification.json) są zachowane.
Lokalnie pobrano ZIP 320717 B i małe publiczne JSON, bez odtwarzania źródłowej
bazy lub całego pipeline. SHA selection pozostał f210b23bb62ef1b1391f7a97b4122c140d8af548316e970c376b22f33d209871.
Pierwszy run wykrył ścieżkę importu o poziom za wysoko; poprawka odróżnia
pakiet importu od zagnieżdżonego snapshot i obejmuje oba orchestratory.

## Zasady przed TEST

[Kampania v2](../reference/stockout-final-campaign-v2.md) zawiera dokładne zasady
ostrzeżenia tylko dla kategorii n<100, z 0,15<ECE≤0,20, zaliczonym AP/Brier
i wsparciem obu klas. Całość, magazyny, zapas i scenariusze nadal mają ECE≤0,15.
Collector i verifier ponownie obliczają ostrzeżenie z metryk; zmiana samej
flagi lub jej budżetu nie przechodzi odbioru. Karta zachowuje ostrzeżenia.

16 testów granic i zgody, 5 testów porównania oraz 2 testy wyboru publicznego
smoke przechodzą. Szerszy zakres AI08 przechodzi: 191 testów w 41,95 s.
Mypy sprawdza 461 plików; Ruff/format i dokumentacja przechodzą.
Końcowy workflow ma max-parallel=6, zachowuje podwójny native/wheel replay
i wykonuje rzeczywistą kwalifikację publicznych wejść dopiero po zaliczeniu jakości.

Publikacja zgody v2 uruchamia pierwszą końcową ocenę. Jej wynik, prawdziwa
kwalifikacja, oddzielny review/integracja oraz końcowe CI wymagają rzeczywistych
receipts. Na tym przyroście **AI 08 pozostaje not ready**.
