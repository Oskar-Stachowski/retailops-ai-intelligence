# AI 08 — rzeczywisty odbiór pełnej ścieżki modelu

[Rzeczywisty run 37281251759](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37281251759)
na commicie `68a3ede16da4bd8b93609c2489514c08e95a7384`, job `111669629844`,
zakończył się sukcesem. To właściwy zamrożony LR/upstream/conditional sigmoid,
kwalifikowany na sześciu końcowych źródłach; nie mechanics fixture. Środowisko
było jednorazowe, na izolowanym runnerze GitHub, z własnymi usługami i uprawnieniami.

[Receipt](08-27-accepted-model/acceptance.json) oraz
[sprawdzenie ZIP/SHA256](08-27-acceptance-download-verification.json) wiążą
artefakt `11331952868`, 32988B, digest
`5fff111424873f8f322c542091f185a1f3c729c63913b414e0f8667b9c279a3d`.

## Co rzeczywiście zaliczono

- Ponowne sprawdzenie oryginalnych sześciu successful evaluate i całej kwalifikacji.
- 206 focused tests, 0 failures, 0 skipped, pełny Gitleaks historii.
- Budowa i sprawdzenie zainstalowanego obrazu: frozen code/lock oraz public adapter.
- 12 rzeczywistych [bramek review](08-27-accepted-model/reports/protocol.json),
  dokładne receipts wszystkich plików oraz [oddzielny approval](08-27-accepted-model/approval.json).
- Pełny importer MLflow, checksum 25 plików, idempotentne ponowienie importu.
- Register v1/v2/v3, promote v1/v2, rollback do v1, reject v3; rzeczywisty PostgreSQL
  i MLflow w namespace `retailops-stockout-risk`, środowisko `test`.
- Autoryzowany HTTP submit 202, ta sama tożsamość przy ponowieniu, prawdziwy cold
  worker, atomowa publikacja i trwały status succeeded.
- Pełne 40 wyników batch, 15 pozycji attention_queue; [rzeczywisty przykład API](08-27-accepted-model/api-attention-example.json).
- HTTP401 bez uwierzytelnienia i HTTP404 dla obcego zakresu fizycznego.
- Quality status `passed_at_publication`; historyczne wyniki mają uczciwe freshness
  `stale`. Nie nadano niezmierzonego globalnego watermark.

Część review/usług trwała 55.72s, bez nowego treningu, źródeł, kalibracji lub
final evaluation. Porównanie przenośnego smoke miało maksymalną różnicę
`2.220446049250313e-16`, limit `1e-12` tylko probability i dokładne pozostałe pola.
Model, progi, capacity i kryteria jakości pozostają niezmienione.

Image digest:
`sha256:441403e308e2a3dd57bb97480fb0677a3ac485edb773ad9ad643de65338543c9`.
MLflow run: `f4f7493bfff343a1bcf3d8d6e6953ac4`.
Batch run: `run-ffabc320c5541140fe8799cd1208f35d`.
Wersje v1/v2/v3 są wersjami tej samej zamrożonej receptury w teście protokołu;
nie oznaczają trzech dodatkowo wytrenowanych modeli.

Wszystkie własne jednorazowe zasoby runnera zostały posprzątane. Nie zmieniono
bazy/MLflow drugiej sesji ani lokalnego Dockera. `production_deployed=false`.
Gotowy etap implementacji i jego odbiór nie są wdrożeniem produkcyjnym.

## Pełne zamknięcie etapu

Pozostały wyłącznie Required CI, normalne scalenie PR14 i odbiór main. Nie ma
pozostałej pracy nad jakością, modelem, polityką, danymi, lifecycle lub API.
[Wynik jakości](08-25-final-campaign-results.md) zachowuje 90 kontroli passed,
3 zaakceptowane warnings i 0 blockers. [Kwalifikacja/karta](08-26-qualified-model.md)
wiąże model i prawdziwe wejścia. Poprzednie scope receipts i nieudane próby
pozostają historycznymi dowodami, nie są przepisywane na sukces.
