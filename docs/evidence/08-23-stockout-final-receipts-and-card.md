# AI 08.23 — końcowe receipts, karta i jawny odbiór

Poprzedni opublikowany HEAD 549df9372e94fa2dc34fe31914dfe40becc85800 zaliczył
[pełny Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37255913192):
2478 tests passed w 2792,98 s, Mypy 447 plików, Ruff/format oraz persistence i secrets.
Worker/HTTP/priority/full restore mają osobny rzeczywisty
[odbiór 37257129973](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37257129973).
Nowy zakres wymaga własnego pełnego CI; wcześniejsze receipts nie dowodzą zaliczenia
końcowej kampanii ani kwalifikacji rzeczywistego modelu.

## Zbieranie sześciu światów

Supervisor zapisuje Git commit, run/attempt, code/lock, limity, pomiary, status
native/wheel oraz SHA i rozmiar dwóch raportów i dwóch access audits. Collector
wymaga sześciu kompletnych światów jednej kampanii i jednego wykonania. Odmienne
raporty native/wheel, przekroczone zasoby, utracony wpis zgody lub pomieszany run
blokują odbiór. Segment/scenario gates są ponownie obliczane z metryk; zmiana
samych flag lub ponowne zahashowanie raportu nie ukrywa niezaliczonej bramki.

Każdy świat pozostaje osobny. Wynik not_ready może zostać zapisany jako poprawny
raport: exit 0 nie jest akceptacją jakości. Pliki final_quality i execution_evidence
są prywatne, atomowe, immutable i łącznie ograniczone do 16 MiB. Collector nie
otwiera source ZIP, rodziców ani wektora końcowych etykiet.

Workflow zbiera receipts po sześciu zakończonych ewaluacjach. Download wskazuje
konkretny run i prefiks dokładnego Git SHA, zgodnie z
[dokumentacją GitHub CLI](https://cli.github.com/manual/gh_run_download).
Archiwa pozostają nieotwieranymi plikami na odizolowanym runnerze. Zbieranie ma
oddzielną rezerwę 6 GiB oraz 512 MiB na pobrane artefakty; nie uruchamia lokalnego
pipeline poniżej rezerwy 50 GiB. Brak owner permission nie uruchamia final workflow.

## Karta i kwalifikacja

Końcowa karta wiąże zamrożony development content/selection ID, przenośny model,
pełny kalibrator, współczynniki LR, odrzucone warianty, progi/capacity/cost,
wszystkie końcowe światy/segmenty/scenarios, coverage i lineage smoke. Pokazuje
ograniczenia syntetycznych źródeł, nakładających się okien, źródłowej freshness,
nieprzyczynowych wyjaśnień oraz kosztów w jednostkach umownych. Selekcyjne metryki
nie są przedstawiane jako niezależna jakość.

Qualifier odmawia przy not_ready przed odtwarzaniem rodziców inference. Dopiero
zaliczona kampania pozwala na pełny public replay features/upstream na własnym
runnerze GitHuba, smoke bez fitowania oraz paczkę qualification. Przynajmniej
jeden punkt smoke musi rzeczywiście kwalifikować się do oceny incident-risk.
Kwalifikacja jest ważna jeden dzień i sama ma serving_eligible=false.

Oddzielny approve wymaga uwierzytelnionego promotera i kompletu 12 jawnych raportów
review gates. Produkcyjna paczka ma 25 plików, w tym pełny selection i execution
evidence. Verifier odtwarza kartę, metryczne gates, cold receipts i smoke. Ponowny
hash zmienionego access audit nadal blokuje odbiór. Import MLflow i każda decyzja
register/reject/promote/rollback są osobnymi autoryzowanymi komendami; żaden builder
ani collector nie zmienia aliasów lub aktywnego DB head.

## Rzeczywisty stan

15 nowych testów odbioru plików/CLI/karty/blokad przechodzi w 8,37 s. Obejmują
complete six-world receipts, niezaliczoną jakość, zmienione bajty, brak raportu,
rozbieżność native/wheel, brak equality, przekroczenie RAM, inny run, niepełny
audit, sfałszowane gates, rehashed audit, atomowe immutable output i brak uprawnień.
Testy używają jawnie syntetycznych danych mechanics-only.

Szerszy odbiór final/registry/lifecycle/batch/API/worker/priority przechodzi:
170 tests passed w 41,65 s. Mypy sprawdza 457 plików src/scripts bez błędów;
Ruff/format obejmują 759 plików. Gitleaks sprawdził 26,90 MB bez sekretów.
Documentation links i required-CI contract przechodzą. Nie zmieniono
zamrożonego evaluator code digest.

Wszystkie sześć rzeczywistych źródeł oraz 9296 końcowych membership jest przygotowane.
Kampania 28bcfec3, code/lock i recipe/policy pozostają niezmienione. Zgoda właściciela
na tę kampanię i politykę nadal oczekuje. Nie odczytano końcowych outcomes, nie
utworzono rzeczywistej końcowej kwalifikacji i nie aktywowano modelu. Nowe final
orchestration/card/qualification muszą otrzymać rzeczywiste receipts po zgodzie.
Niezależna jakość, odbiór/promocja, review/merge i main CI są otwarte.
**AI 08 pozostaje not ready.**
