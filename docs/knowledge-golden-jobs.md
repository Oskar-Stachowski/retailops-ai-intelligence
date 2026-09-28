# Zatwierdzony profil i run golden evaluation

Profil `approved_corpus_fake_golden_validation` wiąże zatwierdzony korpus,
etykiety, zamrożone progi, konfigurację retrieval i decyzje podobieństwa.
Worker zapisuje kandydata oraz pełny raport, także gdy jakość nie przechodzi
progów. Nie aktywuje retrieval. [Odbiór](evidence/11-golden-jobs.md),
[administracja HTTP](knowledge-administration.md), [status](STATUS.md).

## Zgody dla obecnego snapshotu

Właściciel udzielił zgód w sesji 2026-09-28: „wykonaj kolejny krok, masz ode
mnie wszystkie zgody”. Zapisano osobne, związane checksumami decyzje:
[korpus](../knowledge/corpus-approval.v1.json) oraz
[golden labels](../knowledge/golden-labels-approval.v1.json).
Dotyczą 29 dokumentów/451 fragmentów, snapshotów AI `082bed4` i RetailOps
`78f801f` oraz 44 pytań bieżącego golden set. Nie są zgodą na jakość fake,
zmianę progów lub aktywację. Zmiana przypiętych wejść wymaga nowej decyzji.

Niemodyfikowalne wejścia zachowują `review_state=proposed`; aktualną akceptację
reprezentują oddzielne decyzje `approved`. Hash chroni integralność artefaktu,
nie jest podpisem reviewer. Rejestracja wymaga kontrolowanego procesu
właściciela albo jawnie zaakceptowanego pipeline.

## Przygotowanie i rejestracja

```bash
.tools/bin/uv run --locked retailops-ai knowledge-profile-prepare \
  --candidate .local/rag/index-18c7ac743d35.json \
  --golden-set knowledge/golden.v1.json \
  --corpus-approval knowledge/corpus-approval.v1.json \
  --golden-approval knowledge/golden-labels-approval.v1.json \
  --similarity-policy knowledge/similarity.v1.json \
  --similarity-review knowledge/similarity-review.v1.json \
  --output .local/rag/approved-golden-profile.json
```

Przygotowanie działa offline, bez settings, DB lub AWS. Sprawdza środowisko
`local/test`, owner, index/golden/config IDs, obie zgody oraz pełne pokrycie
decyzjami aktualnego raportu podobieństwa. Odtwarza indeks i sprawdza sekcje
expected/forbidden. Nie zmienia pytań ani progów na podstawie wyników rankera.
Niska jakość nie blokuje przygotowania: jej pomiar jest zadaniem runa.

Profil zawiera pełny tekst źródeł, więc pozostaje poza Git w prywatnym pliku
`0600`. CLI odmawia nadpisania istniejącego pliku; wypisuje tylko IDs,
purpose i przypięty request. [Schema profilu](../contracts/knowledge/v1/golden-index-build-profile.v1.schema.json).
`index_config_id` obejmuje również retrieval, similarity policy/review i obie
zgody. `evaluation_set_id` jest rzeczywistym golden set ID.

`knowledge-profile-register --profile … --ai-repo … --retailops-repo …`
odtwarza źródła z przypiętych Git commits przed rejestracją w bazie.
Kontrolowany pipeline może wykonać tę samą kontrolę źródeł na hoście,
a następnie zarejestrować identyczny profil przez adapter w kontenerze
utrzymaniowym. HTTP nie przyjmuje tekstów ani profili; request zachowuje
cztery pola opisane w instrukcji administracji.

## Wynik workera i odczyt raportu

`knowledge-index-work --run-id …` odtwarza kandydata, waliduje mechanikę
i wykonuje golden evaluation. Spełnienie wszystkich mierzonych progów daje
`succeeded` z outputem `candidate`. Niezaliczony próg daje terminalny
`failed/gate_failed`, bez outputu, z zachowanym pełnym raportem.
Retry terminalnego runa odczytuje ten sam wynik i raport, bez nowego pomiaru.

```bash
.tools/bin/uv run --locked retailops-ai knowledge-index-report \
  --run-id run-… --output .local/rag/golden-run-report.json
```

Odczyt wymaga settings bazy i właściwego środowiska. Eksport ma `0600`,
nie nadpisuje pliku i wypisuje tylko report ID. Nieistniejący run/raport daje
bezpieczny błąd. [Schema raportu](../contracts/knowledge/v1/golden-index-run-report.v1.schema.json).

Raport zewnętrzny zawiera faktyczne zgody. Wewnętrzny `GoldenReport` zachowuje
dotychczasowy format pomiaru offline, w tym `corpus_approved=false`,
`labels_approved=false` i `activation_allowed=false`; te pola nie reprezentują
decyzji właściciela zapisanych w profilu. Groundedness odpowiedzi oraz wykonanie
narzędzi agenta pozostają niemierzone. Sukces syntetycznego fixture potwierdza
mechanikę, nie semantyczną jakość projektu.

## Trwałość i bramka bazy

Migracja `0007_rag_golden_jobs` zachowuje wcześniejsze profile `test/fake`.
Wiąże raport z profilem, jego zgodami, manifestem i politykami. Constraint
pozwala zachować raport nieudanego runa wyłącznie przy `gate_failed`;
nie pozwala nadać takiemu runowi outputu. Trigger sukcesu porównuje metryki
z zamrożonymi progami **profilu**, więc sama zmiana flag podsumowania nie daje
sukcesu. Profile, raporty, piny i terminalna historia pozostają niemodyfikowalne.
Downgrade odmawia działania przy istniejącej historii jobs.

Obecny rzeczywisty fake nadal nie przechodzi Recall@5/MRR. Nie ma aktywnego
indeksu lokalnego korpusu. Profil służy zatwierdzonemu pomiarowi golden;
kwalifikacja użytkowego retrieval wymaga odebranej jakości oraz własnej
polityki aktywacji. `fake-release-preflight-v1` nadal zwraca stałe blokady
real provider/użytkowej kwalifikacji. Real embeddings, Bedrock i agent
należą do etapu 12.
