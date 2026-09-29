# AI 04.8 — plikowy run dowodowy i handoff do AI 05

Każdy eksport bierze **już istniejące** artefakty source, curated, features,
backtestu i quality. Weryfikuje ich lineage i kopiuje je do jednego katalogu
`data/generated/forecast-runs/<run_id>/`. Archiwum zawiera wszystkie predykcje,
modele i preprocessing, splity, etykiety, metryki oraz bramki. Poza kopiami
rodziców zapisuje `run_manifest.json`, `config.json`, `metrics.json`,
`model_card.json`, `signature.json`, `input_example.json` i `handoff.json`.
Manifest ma SHA-256 i rozmiar **każdego** pliku. Publikacja jest atomowa,
bez nadpisywania istniejącego runu. Powtórny eksport o tym samym ID musi
mieć identyczne bajty artefaktów.

Run ma typ `forecast_evidence_export`. Jego `started_at` i `completed_at`
mierzą **eksport**, nie historyczny trening. `run_status=export_succeeded`
oznacza powodzenie archiwizacji, a nie przejście bramki jakości. Dawne fit
receipts zachowują czas ścienny/CPU i peak RSS poszczególnych modeli, ale
poprzednie artefakty nie zapisały dokładnego początku/końca każdego treningu
ani treningowego run ID. W manifestcie te pola są `null`; nie uzupełniamy ich
na podstawie czasu utworzenia pliku. Cold-load i inference latency również
nie były mierzone w 04 i wymagają osobnego pomiaru przed servingiem.

`config.json` wiąże okno origin, horyzonty, schemat cech, rozłączne splity,
cutoffy, hiperparametry i piny kodu. `metrics.json` zbiera pooled MAE/WAPE,
wybory per fold, resource receipts i wskazuje pełne segmenty oraz bramki.
`signature.json` podaje wejście i lokalizację predykcji/pipeline’ów;
`input_example.json` jest typowanym przykładem cech, wyłącznie do inspekcji
schematu. Pełne drzewa RF/HGB, preprocessing i ich checksums pozostają
w `backtest/comparison/`. `quality/` zawiera przedziały i uzasadnienia
wszystkich bramek.

```bash
.venv/bin/python -m retailops_ai.forecasting.cli run-export \
  --source-dir data/generated/snapshots/<source_dataset_id> \
  --curated-dir data/generated/curated/<curated_dataset_id> \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --backtest-dir data/generated/forecast-backtests/<backtest_id> \
  --quality-dir data/generated/forecast-quality/<quality_id> \
  --ai-commit <40-znakowy-SHA-commita-kodu>
.venv/bin/python -m retailops_ai.forecasting.cli run-verify \
  --run-dir data/generated/forecast-runs/<run_id>
make forecast-run-check
```

`run-verify` bada wszystkie checksums, typy i powiązania rodziców, po czym
odtwarza raporty zbiorcze z archiwalnych źródeł. Nie wykonuje nowego fitu
ani ponownie nie otwiera final testu. Bramka CI `forecast-run-check` sprawdza
kontrakt; z `--run-dir` waliduje także pełny run. Archiwum jest lokalnym,
ignorowanym przez Git artefaktem. Wskazany commit kodu ma odzwierciedlać
rewizję używaną przy eksporcie.

AI 05 może zaimplementować ten sam interfejs `ArtifactSink` dla MLflow albo
zaimportować gotowy pakiet. Import zachowuje oryginalne `run_id`, czasy
**eksportu**, parent IDs i sumy; własne `imported_at` zapisuje oddzielnie.
Nie wolno opisywać historycznego treningu jako wykonanego w MLflow.
Ten manifest różni się od AI 01 `RunRecord` dla rzeczywistego treningu/batchu;
adapter AI 05 musi zachować ten typ zamiast podszywać się pod run treningowy.
Rejestracja modelu, alias champion, promocja, batch i serving są oddzielnymi
decyzjami AI 05, nie skutkiem udanego eksportu.

Na obecnym temporalnym fixture quality ma **145 passed, 79 failed, 8 not_ready**.
Pusty koszyk zero, regresje w drugim foldzie i niskim wolumenie oraz za małe
pokrycie przedziału w wysokim wolumenie blokują dopuszczenie. Archiwum
utrwala te blokady; `forecast_model_status` pozostaje `not_ready`.
Aktualny [odbiór](evidence/04-08-handoff.md) wskazuje konkretny run i kontrole.
