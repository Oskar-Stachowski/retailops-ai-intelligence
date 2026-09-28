# Audyt Etapu 11 i gotowość do scalenia

**2026-09-28: obecny zakres offline można skierować do `main` przez PR po
zielonym Required CI. Etap 11 nie jest jeszcze w pełni ukończony.**
Nie znaleziono błędu blokującego scalenie tego zakresu w przejrzanym kodzie.
Użytkowe wyszukiwanie pozostaje zablokowane; audyt nie zatwierdza jego włączenia.
[Zapis weryfikacji](11-audit.json) rozdziela nowe kontrole od wcześniejszych odbiorów.

Audytowany commit: `ceec8f042c8d35d80775a62d766ba2ceafac8042`, branch
`ai/rag-corpus`. Po `git fetch origin` branch ma 26 commitów ponad
`origin/main` (`7940d2ee7c82dee4e99d49755fc7eb898f15b2ca`) i nie jest za nim.
Commit zapisujący ten audyt jest osobną zmianą dokumentacji.

Podstawa: [instrukcja 11](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/78f801f2e8bf33a92007821644ef9649b6c62bd5/docs/plans/ai/etapy/11-rag.md),
[instrukcja 12](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/78f801f2e8bf33a92007821644ef9649b6c62bd5/docs/plans/ai/etapy/12-agent-bedrock.md),
implementacja, kontrakty, testy i lokalne artefakty. Obie instrukcje są identyczne
z odczytanymi plikami aktualnego checkoutu RetailOps `0b6104a`.

## Zgodność z instrukcją

| Punkt | Ocena i granica odbioru |
|---|---|
| 1. Korpus i granice | Zaimplementowane: 29 przypiętych dokumentów z obu repo, allowlisty, wykluczenia, checksums i osobna zgoda właściciela. |
| 2. Statusy i dowody | Zaimplementowane metadane statusu/access i referencje. Kontrola twierdzeń w odpowiedziach agenta należy do 12. |
| 3. Parser i chunker | Zaimplementowane: 451 fragmentów, stabilne identity, zakresy cytatów, pełne mapy i usuwanie nieaktualnych fragmentów z kolejnego kandydata. |
| 4. Embeddings i pgvector | Częściowo: fake, cache i storage działają. Istnieje protokół providera, lecz konfiguracja i wyszukiwanie dopuszczają tylko fake. |
| 5. Candidate i aktywacja | Częściowo: niemodyfikowalny kandydat, atomowy swap i rollback sprawdzone w `test/offline_test`. Brakuje użytkowej kwalifikacji i aktywacji. |
| 6. Ograniczony retrieval | Zaimplementowany i sprawdzony dla indeksów testowych: exact cosine, scope/status/access, live deny, deterministyczne top-k i limit kontekstu. Jakość semantyczna nie jest odebrana. |
| 7. Golden i administracja | 44 zatwierdzone pytania, zamrożone progi, osobne uprawnienia admina, trwałe runy i raporty. Golden fake nie przechodzi jakości; wykonanie agent/tool set należy do 12. |

## Otwarte warunki pełnego odbioru

1. **Ścieżka rzeczywistego providera.** Rozszerzyć wersjonowane kontrakty
   konfiguracji/manifestów, walidację wymiarów oraz wstrzykiwanie providera
   w build i query. [Protokół](../../src/retailops_ai/adapters/embeddings.py)
   już istnieje, ale [konfiguracja](../../src/retailops_ai/knowledge/indexes.py)
   ma `provider=fake`, wymiary 8–64 i oznaczenie jakości fake, a
   [query](../../src/retailops_ai/adapters/knowledge_search.py) tworzy
   `FakeEmbeddingProvider` bezpośrednio. Samo ustawienie model ID nie wystarczy.
2. **Użytkowa kwalifikacja, aktywacja i rollback.** Dodać osobną politykę
   akceptującą kompletny raport jakości, właściwą przestrzeń embeddings i zgody
   dla konkretnego indeksu. Obecny [preflight](../../src/retailops_ai/knowledge/qualification.py)
   zawsze ma `activation_allowed=false`, a
   [lifecycle](../../src/retailops_ai/adapters/index_lifecycle.py) obsługuje
   wyłącznie `test/offline_test`. Potrzebny jest odbiór całej użytkowej ścieżki,
   z awarią, współbieżnym odczytem, odmową dostępu i rollbackiem.
3. **Pomiar jakości z rzeczywistymi embeddings.** Na zatwierdzonych
   44 pytaniach uzyskać wymagane Recall@5 ≥ 0,80 i MRR ≥ 0,60 oraz przejść
   pozostałe bramki. Obecne wyniki obu metryk to **0,0441176471**.
   9/9 kontroli krytycznych i poprawne powiązania cytatów nie zastępują jakości
   wyszukiwania. Nie zmieniać etykiet/progów tylko po to, by uzyskać sukces.

To braki do pełnego odbioru, a nie wykryte obejścia istniejących zabezpieczeń.
Testowe embeddings (`fake`) służą do sprawdzania mechaniki i poprawnie nie otwierają
aktywacji. Rzeczywista integracja embeddings/Bedrock i ograniczony smoke są
zaplanowane w 12; smoke 5–10 przypadków nie zastępuje pełnego golden set.
Ścieżkę providera i politykę można przygotować offline już teraz, a odbiór
semantyczny przeprowadzić przy integracji z 12. Groundedness odpowiedzi
i wykonanie narzędzi pozostają osobnymi bramkami agenta.

## Weryfikacja i jej granice

- Nowa regresja administracji/lifecycle/persistence: **104 testy przeszły**
  w 25,28 s. Przegląd objął również migracje, idempotencję, scope, worker,
  retencję raportów i niezmienność zapisów.
- Nowe odtworzenie z przypiętych Git commits daje identyczny indeks:
  29 dokumentów, 451 fragmentów, 450 rekordów embeddings i 130 wykluczeń.
  Walidacja mechaniczna przechodzi. Ponowna ewaluacja wszystkich 44 pytań
  potwierdza metryki oraz dokładnie trzy blokady preflight:
  `golden_thresholds_failed`, `semantic_provider_required`,
  `user_build_profile_required`.
- Dodatkowe kontrole retrieval potwierdzają limity, całkowitą odmowę i odmowę
  po wcześniejszym odczycie, filtry purpose/status, brak uprawnienia,
  niewłaściwe środowisko i próbę rozszerzenia scope.
- Nowe Ruff/format, Mypy (92 pliki), linki dokumentacji, snapshoty kontraktów,
  Gitleaks git/dir i Compose config przechodzą.
- [Pełny wcześniejszy odbiór](11-golden-jobs.md) ma **627 testów**, pakowanie,
  czysty checkout oraz rzeczywisty PostgreSQL/HTTP, awarie i restarty.
  Kod aplikacji, testy, migracje i konfiguracja CI nie zmieniły się od
  `8df02e6`; różnią się tylko cztery pliki dokumentacji/evidence.
  Wszystkie 28 zapisanych checksum plików zgadzają się z audytowanym stanem.
  Pełnego zestawu i Compose runtime nie uruchamiano ponownie w tym audycie.

Nie wykonywano AWS ani testów odpowiedzi rzeczywistego agenta. Lokalny odbiór
macOS ARM64 nie zastępuje zdalnego CI Linux AMD64 dla nowych commitów.

## Warunek publikacji na main

Odczyt GitHub z 2026-09-28 15:39 UTC potwierdza ochronę `main`: obowiązuje PR,
aktualna gałąź i `required-result`, również dla administratora. Ostatni
[Required CI na main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36396020430)
jest zielony, ale dotyczy fundamentu `7940d2e`, nie Etapu 11.
Zdalny branch `ai/rag-corpus` nie istnieje; brak odbioru CI tego zakresu.

Kolejność publikacji: push `ai/rag-corpus` → PR do `main` → sukces jobs
`checks`, `secrets`, `persistence` i `required-result` dla aktualnego PR → merge
→ potwierdzenie Required CI po push na `main`. W audycie nie wykonano push
ani merge. PR powinien opisywać fundament offline i zachowane blokady aktywacji.

## Dalsze możliwości

1. Opublikować obecny zakres przez PR i zebrać zdalny odbiór CI.
2. Kontynuować 11: najpierw ścieżka rzeczywistego providera, następnie użytkowa
   polityka kwalifikacji i jej testy; pomiar real skoordynować z integracją 12.
3. Równolegle prowadzić AI 03: najpierw typed Parquet, polityka artefaktów
   i immutable exporter w RetailOps, potem kontrakt/fixture, importer
   i curated w AI. Lokalny audyt 02 w checkoutcie RetailOps `0b6104a`
   (`docs/evidence/ai/02/audit/README.md`) potwierdza warunek wejścia do 03.
   Stan publikacji tego drugiego repo nie był weryfikowany w tym audycie.
4. Można wcześniej przygotować adaptery i test doubles 12. Zamknięcie pełnego
   agenta wymaga ukończonych 10 i 11 oraz ich odbiorów; same obecne testy RAG
   nie otwierają całego etapu 12.

RAG i dane można rozwijać równolegle w oddzielnych branchach/worktree.
Zmiany wspólnych kontraktów należy uzgadniać między strumieniami.
