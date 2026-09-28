# Kontrola przed kwalifikacją indeksu

`index-release-check` tworzy jeden niemodyfikowalny manifest gotowości z korpusu,
indeksu, konfiguracji retrieval, golden set i raportu. Sprawdza kompletność oraz
zgodność decyzji przeglądu z przypiętymi artefaktami. Wynik jest materiałem do
przeglądu i porównania konfiguracji; polityka `fake-release-preflight-v1` zawsze
zwraca `status=blocked` oraz `activation_allowed=false`.
[Odbiór](evidence/11-qualification.md), [status](STATUS.md).

## Uruchomienie offline

```bash
.tools/bin/uv run --locked retailops-ai index-release-check \
  --candidate .local/rag/index-18c7ac743d35.json \
  --golden-set knowledge/golden.v1.json \
  --golden-report docs/evidence/11-sources-golden.json \
  --similarity-policy knowledge/similarity.v1.json \
  --similarity-review knowledge/similarity-review.v1.json \
  --output .local/rag/release-review.json
```

CLI nie wymaga settings, DB lub AWS. Opcjonalny `--retrieval-config` zastępuje
konfigurację pakietu, lecz musi zgadzać się z golden set. Nowy plik ma `0600`;
istniejący nie jest nadpisywany. Podsumowanie wypisuje IDs i kody blokad,
bez pytań, treści dokumentów, wektorów lub poświadczeń. Exit 0 oznacza
poprawnie sporządzony manifest, także z blokadami; nie jest zgodą na aktywację.
Niepoprawne lub niespójne wejście daje exit 2, stały kod błędu i brak nowego pliku.

Manifest zawiera pełne metadane korpusu/indeksu, embedding/retrieval config,
golden labels/thresholds oraz oryginalny raport. Nie zawiera tekstu chunków
lub wektorów. Corpus/index/golden/config IDs wiążą źródła i konfigurację.
Release ID jest checksumą całego manifestu; brak timestampu wykonania pozwala
odtworzyć identyczny wynik dla tych samych wejść. Zmiana raportu, w tym jego
pomiarów czasu, daje nowy release ID.

## Jakie kontrole wykonujemy

Mechaniczna walidacja indeksu jest odtwarzana, włącznie z fake vectors.
Golden evaluation wykonuje ponownie wszystkie pytania i porównuje kolejność,
wyniki, chunk IDs, cytaty, role krytyczne i metryki z przekazanym raportem.
Zmodyfikowana metryka lub brak pytania powoduje błąd, nawet przy poprawnych IDs.
Timing pochodzi z oryginalnego pomiaru: p95 musi zgadzać się z czasami pytań,
a wynik progów jest wyliczany ponownie. Nowy czas kontroli nie zastępuje czasu
raportu i nie stanowi jego niezależnej atestacji.

Raport podobieństwa jest odtwarzany z pełnych chunków i jawnej polityki.
[Decyzje techniczne](../knowledge/similarity-review.v1.json) wiążą jego report ID
i owner. Każda dokładna grupa, near pair i powtarzana occurrence wymaga własnej
decyzji z uzasadnieniem. Nieznana/stara decyzja lub duplikat jest błędem;
częściowy przegląd pozostawia `similarity_review_incomplete` z liczbą braków.
`retain_separate_contexts` zachowuje osobne cytaty. Usunięcie/scalenie treści
wymaga zmiany źródeł i nowego builda; manifest nie zmienia kandydata.
Ten przegląd ma `corpus_approval_created=false`.

## Oddzielne zgody i blokady

Opcjonalne `--corpus-approval` przyjmuje istniejący `CorpusApproval`, a
`--golden-approval` przyjmuje [GoldenLabelsApproval](../contracts/knowledge/v1/golden-labels-approval.v1.schema.json).
Zgoda na etykiety obejmuje sekcje, answerability, role/scope, tools i progi;
cały golden set ID wiąże tę zawartość. Sprawdzamy index/config/environment/owner,
UTC oraz checksumę. Human reviewer musi być wskazanym właścicielem przeglądu.
Zmiana pytania, etykiety, progu lub źródła wymaga odpowiedniej nowej decyzji.
CLI nie tworzy zgód ani nie zmienia `review_state=proposed` w rejestrze.

Hash zapewnia integralność, nie podpis lub uwierzytelnienie reviewer.
Decyzje muszą pochodzić z kontrolowanego procesu właściciela/zaakceptowanego
pipeline, jak w [lifecycle](knowledge-lifecycle.md). Techniczny manifest nie
ustanawia zaufanego pipeline i nie wystawia endpointu HTTP lub narzędzia agenta.

| Kod blokady | Co pozostaje |
|---|---|
| `corpus_approval_missing` | Jawna akceptacja źródeł/statusów/access/wyłączeń |
| `golden_labels_approval_missing` | Jawna akceptacja etykiet i zamrożonych progów |
| `mechanical_validation_failed` | Poprawna, odtwarzalna budowa indeksu |
| `similarity_review_incomplete` | Decyzje dla wszystkich wykrytych powtórzeń |
| `golden_thresholds_failed` | Odbiór mierzonej jakości dla zamrożonych etykiet |
| `semantic_provider_required` | Rzeczywisty provider i jego odbiór w etapie 12 |
| `user_build_profile_required` | Użytkowy profil/run oraz polityka kwalifikacji |

Dwie ostatnie blokady są stałe w obecnej polityce. Idealny fake report i obie
zgody nie nadają jakości semantycznej ani prawa do użytkowej aktywacji.
Manifest nie jest wejściem obecnego `index-qualify`; DB lifecycle oraz
[administracyjne profile test/fake](knowledge-administration.md) zachowują
oddzielne kontrakty. Bounded real embeddings/Bedrock i agent są etapem 12.
