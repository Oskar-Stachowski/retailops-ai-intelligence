# Odbiór AI 05.1 — tracking, artefakty i odtworzenie MLflow

Data: 2026-09-29. Branch `ai/05-mlflow-serving`, po połączeniu lokalnego
AI 04.8 z `origin/main` zawierającym ukończony handoff danych AI 06.
[Runbook](../mlflow-store.md) opisuje komendy, retencję i granicę tego zakresu.

Stos z AI 01 ma MLflow **3.16.1** na osobnej bazie PostgreSQL
`retailops_mlflow`/roli `mlflow_app` oraz trwały named volume artefaktów.
Port hosta jest ograniczony do `127.0.0.1:5010`; baza nie ma portu hosta.
Nowy kontroler tworzy offline kopię obu zasobów, sprawdza jej sumy i odtwarza
do pustego projektu, bez nadpisywania istniejącego magazynu.

Rzeczywisty test utworzył eksperyment, run i plik w MLflow. Po backupie
źródłowy serwer odczytał te same dane. Restore do oddzielnego projektu
PostgreSQL i wolumenu zakończył się sukcesem; uruchomiony tam MLflow
zwrócił ten sam run i identyczne bajty pliku przez HTTP. Po teście źródło
ponownie przeszło odczyt, a kontenery, sieć i wolumeny jednorazowego celu
zostały usunięte. Źródłowe wolumeny i backup zostały zachowane; obraz
testowy może pozostać w lokalnym cache Dockera.

[Raport maszynowy](05-01-local-store.json) zawiera sześć zaliczonych kontroli.
Wykonane polecenia odbioru:

```bash
make compose-up UV=/Users/oskarstachowski/retailops-ai-intelligence/.tools/bin/uv
.venv/bin/python scripts/check_mlflow_store.py
.venv/bin/python scripts/mlflow_store.py verify --bundle .local/mlflow-backups/mlflow-backup-sha256-ff4c2512e8d91db12712fd7ce80cf2ae5ebd7b6e4b3d9f319fc70026393c6c63
```

Pakiet lokalny:
`mlflow-backup-sha256-ff4c2512e8d91db12712fd7ce80cf2ae5ebd7b6e4b3d9f319fc70026393c6c63`.
Dump metadanych ma **127 684 B**, SHA-256
`2780a24eaffd29809a67f83c0bdf7f02037e4301962d80ebd7f40490ab5e3934`.
Archiwum artefaktów ma **10 240 B**, zawiera cztery pliki **136 B** i ma
SHA-256 `1abeaa75a62d7c2d066d084b39f825aef8b5f5cf0b7b3b89b81ab7e45e9fd6d2`.
Pozostałe trzy pliki pochodzą z wcześniejszych prób na zachowanym wolumenie;
końcowy restore przeniósł całą jego zawartość, nie tylko nowy plik testowy.
Oba pliki i manifest są prywatne pod `.local/mlflow-backups/`, poza Git.

Testy negatywne odrzucają naruszony checksum, archiwum ze ścieżką `..`,
symlink, niezgodną konfigurację oraz restore do celu z istniejącymi tabelami.
Odtworzone pliki mają uprawnienia 0600, katalogi 0700. Pełna regresja:
**940 passed / 586,31 s**; po końcowym dopracowaniu 22 testy CI/magazynu
ponownie przeszły. Ruff/format, mypy, pakiet wheel/sdist, dokumentacja,
Compose config i skan sekretów również przechodzą. Produkcyjny harmonogram
backupu, kopia poza hostem i automatyczne usuwanie nie są częścią 05.1.
Run AI 04.8 nadal ma jakość `not_ready`; nie nastąpił import evidence,
rejestracja modelu, promocja ani inference.
