# AI 05.3a — review i odrzucenie evidence przed registry version

Lokalna nazwa MLflow Registry to `retailops-demand-forecast`. Obecnie nie ma
zarejestrowanej wersji ani aliasów `candidate`, `champion`, `rollback`.
Historyczny eksport AI 04.8 ma status jakości `not_ready`; powodzenie importu
nie uprawnia do rejestracji modelu. [Odbiór](evidence/05-03-review.md)
pokazuje realne review, decyzję i backup.

`review` jest odczytem. Sprawdza status importu, typ runu, ID, czasy,
sumy manifestu i archiwum, raporty, liczniki bramek oraz eligibility.
Zwraca stabilny `review_id` i przyczyny. Nie przelicza metryk ani nie
interpretuje `FINISHED` importu jako zaliczonej jakości.

`reject` wymaga jawnych run ID, decision ID i uzasadnienia oraz prywatnej
polityki i poświadczenia. Token jest odczytywany z pliku 0600, nigdy z argv.
Grant musi mieć **rolę `promoter` i capability `model:decide`**; sama nazwa
principal podana przez klienta nie nadaje roli. Przykład grantu jest w
[`contracts/access/v1/model-promoter.grant-template.example.json`](../contracts/access/v1/model-promoter.grant-template.example.json).
Po weryfikacji powstaje osobny run audytowy MLflow z treścią decyzji,
SHA-256, aktorem, powodem i review ID. Źródłowy run dostaje tylko wskaźnik
na decyzję. Powtórzenie tego samego decision ID sprawdza również treść
artefaktu i zwraca poprzedni wynik; zmieniona treść lub nieukończony audyt
blokuje operację. Gdy zapis audytu zakończył się przed ustawieniem wskaźnika,
powtórzenie może go bezpiecznie uzupełnić.

```bash
make compose-up UV=/Users/oskarstachowski/retailops-ai-intelligence/.tools/bin/uv
.venv/bin/retailops-ai access-init \
  --grants-file contracts/access/v1/model-promoter.grant-template.example.json \
  --output-dir .local/model-promoter-new --ttl-hours 8
.venv/bin/python scripts/mlflow_registry.py review --run-id <mlflow-run-id>
.venv/bin/python scripts/mlflow_registry.py reject \
  --run-id <mlflow-run-id> --decision-id <decision-id> \
  --reason '<uzasadnienie>' \
  --policy-file .local/model-promoter-new/api-access-policy.json \
  --credentials-file .local/model-promoter-new/api-client-credentials.json
make compose-down UV=/Users/oskarstachowski/retailops-ai-intelligence/.tools/bin/uv
```

`access-init` odmawia zastąpienia istniejącego katalogu. Po wygaśnięciu
tokenu utwórz nowy katalog i użyj jego plików. Dane poświadczeń i audytu
operacyjnego są ignorowane przez Git; audyt MLflow jest objęty lokalnym
backupem metadanych i artefaktów.

To **część AI 05.3**. Mechanizmy wersji, aliasów, release’ów i recovery są opisane w
[AI 05.3b](mlflow-lifecycle.md); ich odbiór używa odizolowanych wersji
testowych. Wciąż trzeba odebrać rzeczywisty kwalifikowany model i runtime. Audyt w MLflow jest trwały lokalnie i sprawdzany po SHA, ale
administracyjny dostęp do MLflow może go zmienić; workflow
05.3b dodaje niezależny audyt PostgreSQL i mechanizm wznowienia. Wspólny
backup obu magazynów pozostaje do wykonania.
