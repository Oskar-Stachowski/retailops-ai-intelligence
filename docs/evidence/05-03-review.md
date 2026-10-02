# Odbiór AI 05.3a — odrzucenie historycznego runu

Data: 2026-09-30. Branch `ai/05-mlflow-serving`.
Review odczytał z realnego MLflow run
`6a4ba9bb9e714a4cb84362c20895218a` i potwierdził oryginalny pakiet
AI 04.8 `run-77a5e7dbff215895baac1709ded1f73f` oraz sumy manifestu i
archiwum. Wynik: `not_ready`, 145 `passed`, 79 `failed`, 8 `not_ready`.
Review ID: `model-review-sha256-7217aacd4c97544803c8a01dbb7a32171525f32004da839f46291caf747b751d`.
Nie ma podstaw do rejestracji wersji ani promocji.

Prywatna lokalna polityka zweryfikowała rolę `promoter` i capability
`model:decide`. Pierwsze poświadczenie wygasło podczas przerw sesji;
operacja odmówiła zapisu przed utworzeniem audytu. Po wydaniu nowego
ośmiogodzinnego poświadczenia decyzja
`decision-ai05-quality-20260929` z uzasadnieniem
„AI04 failed quality gates and historical export lacks qualified training
provenance” zapisała audit run `5f14ca8c81854add8adfd9a0071963e3`.
Powtórzenie zwróciło `already_rejected` z tym samym run ID. Ponowna
kontrola odczytała i porównała również całą treść `decision.json`.

MLflow ma nazwę `retailops-demand-forecast`, bez utworzonej wersji lub aliasu.
Nie nastąpiła promocja, zmiana runtime, nowy fit ani serving. Backup po
decyzji: `mlflow-backup-sha256-ec3e4c38ba851e6cd8ba99ad04bafeb531c297985e197bed8ee76cf5dcc40d9b`.
Weryfikacja potwierdziła 13 plików artefaktów, w tym `decision.json` runu
audytowego, oraz dump metadanych PostgreSQL. Wolumeny zachowano, usługi
Compose zatrzymano.

Rzeczywiście wykonane komendy:

```bash
.venv/bin/retailops-ai access-init \
  --grants-file contracts/access/v1/model-promoter.grant-template.example.json \
  --output-dir .local/model-promoter-20260930 --ttl-hours 8
.venv/bin/python scripts/mlflow_registry.py review \
  --run-id 6a4ba9bb9e714a4cb84362c20895218a
.venv/bin/python scripts/mlflow_registry.py reject \
  --run-id 6a4ba9bb9e714a4cb84362c20895218a \
  --decision-id decision-ai05-quality-20260929 \
  --reason 'AI04 failed quality gates and historical export lacks qualified training provenance' \
  --policy-file .local/model-promoter-20260930/api-access-policy.json \
  --credentials-file .local/model-promoter-20260930/api-client-credentials.json
.venv/bin/python scripts/mlflow_store.py backup
.venv/bin/python scripts/mlflow_store.py verify \
  --bundle .local/mlflow-backups/mlflow-backup-sha256-ec3e4c38ba851e6cd8ba99ad04bafeb531c297985e197bed8ee76cf5dcc40d9b
```

[Raport maszynowy](05-03-local-review.json) zawiera stałe identyfikatory.
Pełna regresja: **949 passed / 534,30 s**. Cztery testy tego zakresu
obejmują wiązanie raportów i parametrów z manifestem, odmowę niewłaściwej
roli lub pliku poświadczeń, konflikt nieukończonej decyzji i bezpieczne
ponowienie. Ruff/format (277 plików), mypy (160 plików źródłowych),
schematy dostępu, dokumentacja, wheel/sdist i skan sekretów przeszły.
