# AI 05.1 — lokalny MLflow i odtwarzalny magazyn

Tracking z AI 01 działa już na MLflow 3.16.1. Serwer korzysta z osobnej bazy
PostgreSQL `retailops_mlflow` i roli `mlflow_app`; binaries trafiają do
trwałego wolumenu `mlflow_artifacts`. Baza domenowa RetailOps nie jest
podłączona. [Backup całego lifecycle 05.3c](lifecycle-backup.md) dodatkowo
obejmuje niezależną bazę aplikacji AI. Wersje serwera, obrazu i sterownika
są przypięte w
`Dockerfile.mlflow`, `compose.yaml` i `uv.lock`; port 5010 jest związany tylko
z loopback. [Lokalny stos](local-stack.md) opisuje uruchomienie i uprawnienia.

## Backup i odtworzenie

```bash
make compose-up
.venv/bin/python scripts/mlflow_store.py backup
.venv/bin/python scripts/mlflow_store.py verify --bundle .local/mlflow-backups/<backup_id>
.venv/bin/python scripts/mlflow_store.py restore \
  --bundle .local/mlflow-backups/<backup_id> \
  --target-project retailops_ai_<nazwa_pustego_projektu>
make mlflow-store-smoke
```

`backup` wymaga działającej bazy i zmigrowanego MLflow. Na czas kopiowania
zatrzymuje serwer MLflow, wykonuje `pg_dump` bazy metadanych i eksportuje
wolumen artefaktów przez jednorazowy kontener. Po zakończeniu przywraca serwer
do poprzedniego stanu. Nie kopiuje samego wolumenu PostgreSQL ani bazy AI;
kopię bazy AI wraz z MLflow zapewnia [procedura 05.3c](lifecycle-backup.md).
Pakiet pod `.local/mlflow-backups/`
zawiera `metadata.dump`, `artifacts.tar` i manifest z SHA-256 obu plików,
wersją kontraktu, identyfikatorem projektu i pinami konfiguracji.
Katalog ma uprawnienia 0700, pliki 0600; pakiet nie trafia do Git.

`verify` działa bez Dockera: sprawdza zawartość i sumy, nagłówek dumpu,
liczbę i bezpieczne nazwy plików w archiwum. Odtworzenie wymaga **pustej**
bazy MLflow i pustego wolumenu w wskazanym projekcie Compose. Polecenie
sprawdza też zgodność bieżących `compose.yaml` i `Dockerfile.mlflow` z pinami
pakietu; starszy pakiet wymaga uruchomienia z przypiętej rewizji kodu albo
jawnej procedury migracji. Może utworzyć bazę i obraz w nowym projekcie,
ale odmawia nadpisania
działającego serwera lub istniejących tabel/plików. Przywraca metadane
transakcyjnym `pg_restore`, następnie artefakty; pozostawia serwer zatrzymany
do weryfikacji i jawnego uruchomienia. Nie ma transakcji obejmującej zarazem
PostgreSQL i wolumen. Jeśli restore przerwie się między tymi krokami,
nie uruchamiaj MLflow z częściowym celem; użyj kolejnego pustego projektu
i zweryfikowanego pakietu.

`mlflow-store-smoke` uruchamia prawdziwy serwer, zapisuje eksperyment, run
i plik, robi backup, odtwarza go do jednorazowego projektu i odczytuje run
oraz identyczne bajty artefaktu przez HTTP. Na końcu usuwa kontenery,
sieć, wolumeny i własny tag obrazu jednorazowego celu, także po błędzie testu.
Współdzielony cache budowania pozostaje pod kontrolą Dockera.
Źródłowe wolumeny i backup pozostają. Test jest częścią
Required CI `persistence`. [Odbiór lokalny](evidence/05-01-store.md) zawiera
identyfikator zweryfikowanego pakietu i wynik.

## Retencja i granica etapu

W 05.1 nie działa automatyczne usuwanie runów, artefaktów ani backupów.
Zachowujemy runy i wszystkie wersje potrzebne do kandydatury, championa,
rollbacku lub audytu. Przed ręcznym usunięciem starszego backupu trzeba
mieć nowszy zweryfikowany backup oraz udany test restore; usunięcie
aktywnego/rollback artefaktu jest zabronione. To jawna konserwatywna polityka
lokalna. Automatyczny harmonogram, szyfrowana kopia poza hostem, quota i
docelowy czas retencji wymagają osobnej decyzji operacyjnej przed produkcją.

AI 05.1 zapewnia magazyn i odtworzenie. **Nie importuje jeszcze runu 04.8,
nie rejestruje modelu i nie włącza inference.** Następny zakres AI 05.2
ma podłączyć historyczne evidence bez udawania ponownego treningu w MLflow.
Run AI 04 ma bramkę jakości `not_ready`, więc późniejszy import nie może
automatycznie nadać mu statusu candidate/champion ani uprawnień serving.
