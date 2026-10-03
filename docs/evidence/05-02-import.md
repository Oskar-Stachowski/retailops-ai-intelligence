# Odbiór AI 05.2 — historyczny run w MLflow

Data: 2026-09-29. Branch `ai/05-mlflow-serving`. Importer z
[runbooka](../mlflow-evidence.md) wczytał istniejący pakiet AI 04.8
`run-77a5e7dbff215895baac1709ded1f73f`, bez ponownego treningu.
Wejście było wcześniej [odebrane w 04.8](04-08-handoff.md): 557 plików
o łącznym rozmiarze 248 675 504 B, manifest SHA-256
`e21fcf75abe4b3465c497e289e4a01636469464d53cef92a58e0528600893c4a`.

MLflow utworzył run `6a4ba9bb9e714a4cb84362c20895218a` w eksperymencie
`retailops/forecast-historical-evidence`. Import zawiera deterministyczne
archiwum 558 plików (wraz z manifestem), **26 740 707 B**, SHA-256
`d3131b3d89718b9ec91c6e9f8530c9b22e1f3d810856a0559358185059bdfc8d`.
Dołączono też osobno `run_manifest.json`, config, metrics, model card,
handoff, signature i input example. Wszystkie osiem artefaktów zostało
odczytanych z MLflow i porównanych z lokalnymi checksumami. [Raport
maszynowy](05-02-local-import.json) podaje identyfikatory i kontrole.

Wpis zachowuje oryginalny run ID i czasy **eksportu** jako tagi, a czas
importu jako osobny `imported_at`. Parametry zawierają source/curated/feature/
label/split/backtest/quality IDs, SHA źródła i repo AI, lock i config hash,
seeds oraz sumy manifestu i archiwum. Bramka pozostaje **145 passed,
79 failed, 8 not_ready**; pełne metryki i ich ważność pozostają w
oryginalnych raportach. `FINISHED` odnosi się do importu, nie do jakości.
Żaden model nie został zarejestrowany ani promowany.

Powtórne wywołanie tej samej komendy dało `already_imported` z tym samym
MLflow run ID i SHA archiwum; nie utworzyło drugiego runu. Import odrzuca
częściowo ukończony lub sprzeczny wpis zamiast przepisywać go automatycznie.
Testy obejmują deterministyczność archiwum, nienadawanie eligibility,
zachowanie czasów i konflikt duplikatu. Lokalne runy i artefakty MLflow
są na zachowanych wolumenach; nie należą do Git. Po imporcie utworzono i
zweryfikowano backup 05.1 `mlflow-backup-sha256-f52d6b00def93118a19ab1f485c10964bf959e5880d5ca58f57ceb4038db11ed`
z 12 plikami artefaktów (w tym ośmioma z importu) oraz dumpem metadanych.
To lokalna kopia, bez automatycznego harmonogramu ani kopii poza hostem.

Polecenie odbioru (wykonane dwukrotnie):

```bash
.venv/bin/python scripts/mlflow_evidence.py \
  --run-dir /private/tmp/retailops-ai-04-forecasting/data/generated/forecast-runs/run-77a5e7dbff215895baac1709ded1f73f
```

Pełna regresja: **945 passed / 591,90 s**. Ruff, format (273 pliki), mypy
(159 plików źródłowych), sprawdzenie linków/Required CI, wheel/sdist i skan
sekretów również przeszły. Import oraz backup działały na rzeczywistym
lokalnym PostgreSQL/MLflow; usługi zatrzymano, wolumeny zachowano.
