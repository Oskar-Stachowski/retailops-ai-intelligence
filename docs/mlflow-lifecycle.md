# AI 05.3b — wersje, decyzje i zatwierdzone release’y

Mechanizmy Registry działają z PostgreSQL i MLflow: rejestracja wersji,
`candidate/champion/rollback`, review, reject, promote i rollback.
[Odbiór](evidence/05-03-lifecycle.md) dotyczy oddzielnej nazwy
`retailops-demand-forecast-mechanics` i jednorazowej bazy. To test mechaniki,
bez zatwierdzenia jakości prognoz. Właściwy `retailops-demand-forecast`
nadal nie ma kwalifikowanego modelu ani release’u.

## Kwalifikacja i niezmienne przypięcia

Źródłem jest wyłącznie run w zaufanym lokalnym MLflow; operator podaje run ID,
evidence ID oraz SHA-256 kapsuły. Nie przyjmujemy URL, ścieżki do pickle ani
nazwy aliasu zamiast numeru wersji. [Schematy](../contracts/model_lifecycle/v1/qualification.schema.json)
i walidatory odrzucają nieznane pola i niepełną listę gates.

Kapsuła w `lifecycle/` zawiera `qualification.json`, `model.json`,
`config.json`, `signature.json`, `input_example.json` oraz raporty
`gate_<nazwa>.json`. Wiąże sumy i rozmiary artefaktów, dataset/feature/label/split,
code SHA, lock hash, seeds, konfigurację i oryginalne czasy. Każdy raport
wskazuje ten sam evidence i checksum modelu. Wszystkie wymagane gates
(source, features, PIT, protocol, segments, signature, resources,
security/license, model card, freshness/drift compatibility) muszą mieć
`passed`; brak próbki lub `not_ready` blokuje operację. Compatibility ma
wersjonowany reference i niewygasły `valid_until`; nie oznacza uruchomienia
pełnego monitoringu z AI 13.

Dla rzeczywistego forecastu sprawdzamy istniejący `QualityManifest` AI 04,
feature/model binding i wykonujemy load/inference smoke na przypiętym
przykładzie. Nie implementujemy drugiego evaluatora ani nie przeliczamy
wyników na zbiorze końcowym. Obsługujemy istniejący przenośny pipeline RF/HGB
oraz bezpieczny JSON recipe baseline’u korzystający z predyktora AI 04.
Model bazowy może zostać championem po osobnej kwalifikacji.

Kapsuła jest zatwierdzonym wynikiem upstream review, nie narzędziem do
wytwarzania pozytywnych dowodów z samej deklaracji operatora. Wciąż trzeba
przygotować ją dla rzeczywistego, kwalifikowanego modelu. Historyczne
pochodzenie artefaktu jest dozwolone: zachowujemy `historical_evidence` i
oryginalne czasy, bez fikcyjnego nowego treningu. Obecny eksport 04.8 nie ma
takiej kwalifikacji i nadal ma 145 passed / 79 failed / 8 not_ready.
Dotychczasowe review 05.3a zachowuje treść i identity historycznej decyzji.

Po rejestracji baza AI zamraża binding numeru wersji, źródłowego runu,
artefaktu i kwalifikacji. Przed nową promocją lub rollbackiem ponownie
sprawdzamy rzeczywisty MLflow i artefakty. Zmiana source/run/checksum albo
failed load blokuje operację. Release przypina binding, model/config/schema,
image digest, evaluation i decision oraz poprzedni release/version.
Pierwszy release ma jawnie `previous_release_id=null`, `previous_version=null`.
Rollback kopiuje pełne przypięcia poprzedniego release’u, w tym image digest.
Wygasła lub uszkodzona kwalifikacja poprzedniej wersji blokuje rollback;
nie zastępuj jej arbitralnym lokalnym plikiem.

## Autoryzacja i audyt

Polecenia operatora wymagają zweryfikowanej roli `promoter` i capability
`model:decide`, prywatnej polityki i pojedynczego credential 0600. Token
nie trafia do argv ani logów. Kontener ponownie uwierzytelnia operatora;
rola i principal nie pochodzą z żądania. Nie dodano endpointu HTTP promocji.

Migracja `0009_model_lifecycle` utrwala decyzje, kroki, model bindings,
release’y i zatwierdzony pointer w bazie **AI**, niezależnie od metadanych
MLflow. Triggery blokują UPDATE/DELETE historii. Nie jest to magazyn WORM:
administrator bazy AI może zmienić schemat; administrator MLflow może
zmienić jego metadane/artefakty, co wykrywa ponowna walidacja.

## Recovery

Blokada advisory PostgreSQL serializuje operatorów jednej nazwy modelu.
Przed mutacją powstaje niezmienny request hash i plan before/after. Kroki
są zapisywane trwale; inne decyzje tej nazwy czekają na zakończenie recovery.

Po utracie odpowiedzi z create szukamy wersji po registration decision ID.
Jeśli istnieje dokładnie jedna zgodna wersja, wznowienie nie tworzy kolejnej.
Jeśli zapisano zamiar POST, ale nie ma obserwowalnej wersji, wynik jest
niepewny: operacja wymaga przeglądu przez operatora, bez automatycznego
powtarzania create. Nie ma jeszcze komendy porzucenia takiej decyzji.

Zmiany aliasów wznawiamy ze stanu odczytanego z MLflow. Zachowujemy poprzednią
wersję przez ustawienie `rollback` przed `champion`. Stan spoza planu before/
after blokuje recovery. Zatwierdzony pointer, release i completion audit
commitują razem w bazie AI dopiero po sprawdzeniu aliasów. Nie jest to
transakcja rozproszona z MLflow. Powtórzenie zakończonej dawnej decyzji
zwraca jej wynik i nie przestawia nowszych aliasów ani pointera.

## Polecenia

Wykonany odbiór mechaniki:

```bash
make model-lifecycle-smoke UV=/Users/oskarstachowski/retailops-ai-intelligence/.tools/bin/uv
```

Poniższe komendy są interfejsem dla przyszłego kwalifikowanego modelu;
placeholdery nie oznaczają istniejącej produkcyjnej wersji:

```bash
.venv/bin/python scripts/mlflow_lifecycle.py register \
  --run-id <qualified-mlflow-run-id> --evidence-id <evidence-id> \
  --qualification-sha256 <sha256> --decision-id <decision-id> \
  --reason '<uzasadnienie>' --policy-file .local/<operator>/api-access-policy.json \
  --credentials-file .local/<operator>/api-client-credentials.json
.venv/bin/python scripts/mlflow_lifecycle.py review --version <numer> \
  --policy-file .local/<operator>/api-access-policy.json \
  --credentials-file .local/<operator>/api-client-credentials.json
.venv/bin/python scripts/mlflow_lifecycle.py promote --version <numer> \
  --evidence-id <evidence-id> --qualification-sha256 <sha256> \
  --decision-id <decision-id> --reason '<uzasadnienie>' --image-digest sha256:<image-sha> \
  --policy-file .local/<operator>/api-access-policy.json \
  --credentials-file .local/<operator>/api-client-credentials.json
```

`reject` i `rollback` używają tych samych argumentów co promote, bez
`--image-digest`; rollback odtwarza image z poprzedniego release’u.
Historyczny eksport bez model version nadal obsługuje
[review/reject 05.3a](mlflow-registry.md). Stos musi być uruchomiony i
zmigrowany; provision poświadczeń opisuje ten sam runbook 05.3a.

## Granica odbioru i pozostały zakres

Pointer oznacza **zatwierdzony release**, nie faktycznie działający worker.
Odpowiedzi mają `runtime_status=not_integrated`. Integracja modelu z procesem,
persisted batch, odczyt prognoz i failure/load rollback runtime pozostają
w kolejnych zakresach AI 05. Nie wykonano tych operacji na modelu AI 04.

AI 05.3 pozostaje otwarte. Do domknięcia potrzeba rzeczywistej kwalifikacji
modelu i odbioru na jego artefaktach. [Wspólna procedura backup/restore
05.3c](lifecycle-backup.md) obejmuje dane aplikacji AI, metadane MLflow
i artefakty oraz zachowuje niedokończone decyzje do recovery.
Backup 05.1 nadal kopiuje tylko MLflow; nie jest pełnym backupem lifecycle.
[Trwała kolejka 05.4a](forecast-worker.md) ma odbiór mechaniki;
[loader 05.5a](forecast-runtime.md) ma odbiór wejścia i adapterów.
Integracja rzeczywistych profili z workerem i publikacja wyników pozostają
otwarte, podczas gdy poprawa/kwalifikacja AI 04 trwa w osobnym branchu/worktree.
