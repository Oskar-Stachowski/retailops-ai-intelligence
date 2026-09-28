# Odbiór źródeł i etykiet — etap 11

**2026-09-28 · techniczny przegląd zachowuje status propozycji.**
[Pomiar JSON](11-sources.json), [instrukcja](../knowledge-sources.md),
[aktualny status](../STATUS.md).

Kontrola golden set wiąże także sekcje zabronione z pełnym kandydatem.
Usunięta ścieżka, zmieniony nagłówek lub status powodują błąd przed retrieval.
Duplikaty sekcji oczekiwanych/zabronionych są odrzucane.
40 testów retrieval/golden przechodzi (7,51 s), w tym pięć nowych przypadków
negatywnych. Strict Mypy obejmuje 86 plików źródłowych.

## Zamrożone źródła i metadane

Nowy rejestr wskazuje AI `082bed47e1e6344e3ec148807f2decd1ef47a605` i RetailOps
`78f801f2e8bf33a92007821644ef9649b6c62bd5`, czyli snapshoty wybrane w tym zakresie.
Pozostaje `review_state=proposed`. Obejmuje **29 dokumentów / 451 fragmentów**:
17 dokumentów AI i 12 RetailOps. Dziewięć nowych pozycji to siedem instrukcji
RAG oraz dwa dowody z jawnym zakresem wcześniejszych pomiarów.

Wszystkie checksumy dokumentów, code/evidence refs i cytaty zostały odtworzone
z przypiętych blobów, bez worktree. Pięć dotychczasowych dokumentów zmieniło
treść. Status/access istniejących pozycji zachowano; fact scopes doprecyzowano.
Rejestr ma 10 specified, 15 implemented i 4 verified, wszystkie public_project.
Plany nie dowodzą wdrożenia, instrukcje RAG opisują mechanikę test/fake,
a karta RF nadal opisuje rejected bez serving. JSON evidence służy walidacji
metadanych, nie staje się tekstem korpusu.

Dokumentacja seed RetailOps jest jawnie ograniczona do demo/legacy v1.
Nie dołączono `ml-features.md` jako opisu source 2.5: nadal opisuje source 2.3
i otwartą izolację runtime. Spis historycznych fixtures 2.0–2.3 również nie
stanowi odbioru 2.5. Nie zmieniono drugiego repozytorium lub jego dokumentacji.

Nowy kandydat ma ID
`index-sha256-18c7ac743d35a7f9b8d59f781f2f4ccace9e24af79046bcf9f6a36895de038e9`.
Zachowano **294 chunk IDs i rekordy cache**. Osiem dawnych fragmentów nie
występuje w nowym kandydacie; nowe bloki mają nowe IDs. Poprzedni artefakt
nie został nadpisany. Index/chunk graph potwierdza pełne coverage i brak orphanów.
Artefakty offline mają nowe ścieżki i tryb `0600`; nie zapisano nowego kandydata
do DB ani nie zmieniono wskaźnika aktywnego indeksu.

## Podobieństwo i etykiety

Niezależna analiza **406 par dokumentów i 101 025 par unikalnych treści fragmentów**
jest zgodna z raportem. Nie znaleziono near pairs przy progu 0,80 ani dokładnych
powtórzeń dokumentów. Jedno dokładne powtórzenie fragmentu to krótkie
„Z katalogu AI, z RetailOps obok:” w dwóch różnych instrukcjach.
Decyzja techniczna zachowuje oba heading/source contexts oraz jeden rekord
embedding cache. Nie usuwa cytatów i nie nadaje zgody na korpus.

Golden set ma **44 pytania**, w tym osiem nowych o parser/cytaty, administrację,
wznowienie workera, podobieństwo, statusy i odmowę rozszerzenia repo/history scope.
Pytanie o kontrakty seed zawężono do legacy v1. Wszystkie sekcje oczekiwane
i zabronione są związane z nowym kandydatem przed ewaluacją. Progi pozostały
identyczne z wcześniej zamrożonymi; etykiet nie wybierano z rankingu.

[Pełny raport fake](11-sources-golden.json) daje:

| Kontrola | Wynik | Próg |
|---|---:|---:|
| Recall@5 | 0,04412 | ≥0,8 |
| MRR | 0,04412 | ≥0,6 |
| Binding cytatów | 1,0 | 1,0 |
| Przypadki krytyczne | 9/9 | 100% |
| Groundedness odpowiedzi | niemierzone | ≥0,95 |

Wynik ma `measured_thresholds_passed=false`, `activation_allowed=false`,
`corpus_approved=false`, `labels_approved=false`. Binding cytatów potwierdza
zgodność z przypiętym fragmentem, nie prawdziwość twierdzeń. Fake vectors nie
potwierdzają jakości semantycznej. Koszt wywołań modelu offline wynosi 0 USD.
Po odświeżeniu 40 testów retrieval/golden przechodzi ponownie (11,91 s).

Przegląd techniczny metadanych/etykiet nie tworzy akceptacji korpusu i nie
aktywuje indeksu. Pozostają akceptacja właściciela, profil użytkowy, kwalifikacja
golden i release manifest; real embeddings/Bedrock/agent mają odbiór w etapie 12.
Nie wykonano push ani zdalnego Required CI dla tego zakresu.
