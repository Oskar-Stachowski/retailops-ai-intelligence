# AI 05.2 — historyczne evidence w MLflow

Importer przyjmuje **wyłącznie zweryfikowany** pakiet `forecast_evidence_export`
z AI 04.8. Weryfikuje manifest, wszystkie checksums, lineage rodziców i raporty
przed połączeniem z lokalnym MLflow pod `127.0.0.1:5010`. Oryginalny
`run_id` i czasy **eksportu** są zapisane w tagach; natywny czas runu MLflow
mierzy import. Tag `retailops.imported_at` identyfikuje tę późniejszą czynność.
Nie ma tu nowego fitu ani historycznego treningu wykonanego w MLflow.

Eksperyment `retailops/forecast-historical-evidence` zapisuje source/curated/
feature/label/split/backtest/quality IDs, SHA obu repo, hash lockfile i config,
seeds, liczby bramek oraz tylko ważne, niepuste wartości pooled MAE/WAPE.
Pełne metryki z liczebnościami, statusem ważności, baseline'ami i zasobami,
raporty jakości, model card, signature oraz input example pozostają w
oryginalnym pakiecie. Import dołącza siedem czytelnych raportów oraz
deterministyczne archiwum całych 558 plików. Przed oznaczeniem importu jako
`verified` pobiera je z MLflow i sprawdza SHA-256; ponowny import sprawdza
istniejący run zamiast tworzyć kolejny. Konflikt lub przerwany import wymaga
przeglądu — importer nie zastępuje w ciemno istniejącego evidence.

```bash
make compose-up UV=/Users/oskarstachowski/retailops-ai-intelligence/.tools/bin/uv
.venv/bin/python scripts/mlflow_evidence.py \
  --run-dir /path/to/ai04-worktree/data/generated/forecast-runs/<run_id>
make compose-down UV=/Users/oskarstachowski/retailops-ai-intelligence/.tools/bin/uv
```

Podczas lokalnego odbioru `--run-dir` wskazywał katalog AI 04 worktree,
bez kopiowania 248 MB do repo AI 05. Tylko lokalny loopback może wywołać
REST MLflow. Artefakty i metadata są objęte [backupem 05.1](mlflow-store.md).

`FINISHED` w MLflow oznacza ukończony **import**, a nie ocenę jakości.
Status jakości `not_ready` i brak eligibility do registry/promotion/serving
pozostają jawne. Historyczne modele i ich pipeline'y są w archiwum dowodowym,
ale nie są zarejestrowanymi MLflow Models ani kandydatami release'u. Następny
zakres AI 05.3 musi wykonać osobną kwalifikację i decyzję.
