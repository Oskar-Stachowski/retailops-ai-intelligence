# AI 05 — wspólna kopia i odtworzenie stanu v12

Kopia zachowuje cały utrwalony stan aplikacji w dwóch bazach PostgreSQL
oraz wszystkie pliki magazynu MLflow. Odtworzenie przenosi go do nowego,
pustego projektu. Obejmuje to wersje modeli, decyzje operatora, zadania,
ich wejścia i historię, kompletne wyniki, publikacje oraz oryginalne oceny.

Używamy istniejącego [kontrolera wspólnego backupu](lifecycle-backup.md).
Nie ma osobnego eksportu kilku tabel v12: kontroler sprawdza wszystkie
tabele i sekwencje schematów `ai` i MLflow `public`, również puste.
Zachowuje ich liczby wierszy, SHA-256, aktualną rewizję bazy
oraz checksum całego archiwum plików, także niepowiązanych z aktywnym modelem.
Zestawienie liczby wierszy jest wykonywane jednym zapytaniem na bazę;
limit wierszy i poprawność nazw są sprawdzane przed kopiowaniem treści.
Odbiór historyczny poniżej dotyczy `0019_v12_development`; gałąź
[AI 10](intelligence-integration-v2.md) dodaje `0020_intelligence_outbox`.
Nie stanowi to dowodu odtworzenia rzeczywistej kampanii z nowym head.

## Procedura operatora

W checkoutcie zgodnym z pinami i przy działającym PostgreSQL:

```bash
.venv/bin/python scripts/lifecycle_store.py backup
.venv/bin/python scripts/lifecycle_store.py verify \
  --bundle .local/lifecycle-backups/<backup_id>
.venv/bin/python scripts/lifecycle_store.py restore \
  --bundle .local/lifecycle-backups/<backup_id> \
  --target-project retailops_ai_<nowy_projekt>
```

To operacja utrzymaniowa: backup zatrzymuje API/MLflow, blokuje nowe
połączenia obu ról aplikacji i kończy ich istniejące sesje. Zewnętrzne
workery v12 także tracą dostęp do bazy. Przed operacją zatrzymaj ich
przyjmowanie pracy i pozwól skończyć bieżące zadania. Powstrzymaj również
administracyjne zapisy do baz i bezpośrednie zapisy do wolumenu artefaktów.
Kontroler nie blokuje superusera ani procesów zapisujących pliki poza MLflow.

Po udanym backupie wracają poprzednie limity połączeń i tylko wcześniej
działające usługi. Po SIGKILL kontrolera źródło może pozostać zablokowane;
sprawdź prywatny dziennik maintenance i użyj `lifecycle_store.py resume`.
Nie usuwaj dziennika ręcznie. Kontrola identyfikatora klastra chroni przed
odblokowaniem innej bazy pod tą samą nazwą.

Restore odmawia nadpisania źródła, bieżącego stosu lub istniejącego celu.
Obie bazy celu pozostają zablokowane aż do zgodności wszystkich tabel,
sekwencji i plików. Uszkodzony lub częściowo odtworzony cel zostaje offline;
ponowienie wymaga kolejnego pustego projektu. Nawet po sukcesie usługi
uruchamia się jawnie, zgodnie z [procedurą odtwarzania](lifecycle-backup.md).

## Zadania i niedokończone decyzje

Odtworzenie zachowuje oryginalny input, release, wersję modelu, klucz
idempotencji, próby i terminy zadania. Nie przesuwa deadline, nie przedłuża
kwalifikacji ani lease. Przerwa może więc spowodować wygaśnięcie zadania lub
dopuszczenia; istniejące reguły retry/recovery nadal obowiązują. Przypięta
wersja zadania nie zmienia się wskutek późniejszego przesunięcia aliasu.

Niedokończoną rejestrację wznawia się z tym samym `decision_id` i żądaniem,
zgodnie z [lifecycle v12](mlflow-v12-lifecycle.md). Jeśli MLflow utworzył
wersję przed utratą odpowiedzi, recovery odszukuje tę wersję i kończy
dziennik bez drugiego `create`. Sam backup ani restore nie promują modelu.

Kopia obejmuje dane znajdujące się w bazach i MLflow. Prywatne katalogi
operatora, lokalny wheel/verifier/predictor i jego środowisko, źródłowe
snapshoty/curated poza MLflow, konfiguracja hosta oraz poświadczenia
wymagają osobnego zabezpieczenia. Worker potrzebuje tych zasobów i zgodnych
pinów przed rzeczywistym wznowieniem obliczeń. Restore nie odbudowuje
automatycznie środowiska wykonawczego ani nie pobiera brakujących źródeł.

## Odbiór na osobnych danych

```bash
make v12-backup-smoke
# Lokalnie można wskazać istniejący obraz z Dockerfile.mlflow:
.venv/bin/python scripts/check_v12_backup.py \
  --mlflow-image retailops-ai-mlflow:local
```

Runner używa wyłącznie obecnych, niezmiennych ID obrazów: pgvector/PostgreSQL
oraz MLflow 3.16.1 z `psycopg2-binary` 2.9.11. Nie wykonuje build ani pull.
Brak obrazu kończy test błędem; `compose-smoke` w Required CI wcześniej
buduje właściwy obraz MLflow. Przyrost nie usuwa obrazów ani cache Dockera.

Test korzysta z trzech prywatnych projektów UUID i małych jawnych fixture.
Każdy zasób ma dodatkową etykietę właściciela; cleanup odmawia usuwania
obcego zasobu. Źródło jest zatrzymane przed uruchomieniem celu, dzięki czemu
równocześnie działają najwyżej jeden PostgreSQL i jeden serwer MLflow,
z limitami odpowiednio 512 MiB i 1536 MiB oraz po jednym CPU. Hostowe
porty są losowe i dostępne tylko na loopback. HTTP API sprawdza ASGI TestClient;
test nie uruchamia produkcyjnego kontenera API ani workera z rzeczywistym modelem.

Odbiór obejmuje istniejące kontrole lifecycle, kolejki, publikacji, katalogu
i ocen, a następnie SIGKILL kontrolera backupu, odmowę połączeń aplikacji,
jawne wznowienie, spójną kopię, uszkodzony restore pozostający zablokowany,
pełny restore do pustego celu, recovery utraconej odpowiedzi rejestracji,
wznowienie przypiętego zadania i SIGKILL/restart odtworzonego stosu.
Oryginalny verifier eksportu, predictor, odtworzenie źródeł i kwalifikacja
są małymi doubles. Test nie otwiera bieżącej kampanii AI 04.

Raport to `reports/ai05-v12-backup-acceptance.json`, z oddzielnymi receiptami
JUnit dla źródła, przygotowania pending state, odtworzenia i restartu.
Zweryfikowany pakiet fixture zostaje prywatnie pod
`.local/v12-backup-acceptance/<backup_id>`; nie trafia do Git.
[Evidence](evidence/05-v12-backup.json) wiąże wynik z kodem i granicami testu.
Required CI uruchamia ten zestaw jako ostatni test joba `persistence`,
obejmując także poprzedni `v12-metadata-smoke`.

Odbiór dotyczy mechaniki przechowywania i odzyskiwania. Rzeczywisty końcowy
eksport AI 04, jego źródło inference, kwalifikacja i przegląd operatora,
pełny batch/serving z pomiarami, zdalny Required CI i jawna migracja
trwałego środowiska pozostają osobnymi krokami.
