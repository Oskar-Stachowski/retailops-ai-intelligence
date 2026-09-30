# AI 05.3c — wspólne odtworzenie MLflow i bazy AI

Lifecycle przechowuje dane w trzech miejscach: audyt, bindings i release’y
w schemacie `ai` bazy `retailops_ai`, metadane Registry/tracking w
`retailops_mlflow` oraz pliki w wolumenie `mlflow_artifacts`. Wspólny pakiet
wiąże te zasoby jednym manifestem. [Backup 05.1](mlflow-store.md) pozostaje
narzędziem do samego MLflow; pełny lifecycle korzysta z procedury poniżej.

## Polecenia

```bash
make compose-up
.venv/bin/python scripts/lifecycle_store.py backup
.venv/bin/python scripts/lifecycle_store.py verify \
  --bundle .local/lifecycle-backups/<backup_id>
.venv/bin/python scripts/lifecycle_store.py restore \
  --bundle .local/lifecycle-backups/<backup_id> \
  --target-project retailops_ai_<nowy_projekt>
make lifecycle-store-smoke UV=/Users/oskarstachowski/retailops-ai-intelligence/.tools/bin/uv
```

`backup` wymaga działającego PostgreSQL oraz zmigrowanych obu baz.
Zapisuje wcześniejsze limity połączeń, identyfikator klastra i listę
uruchomionych usług w prywatnym dzienniku maintenance, zatrzymuje API/MLflow,
ustawia dla obu baz `CONNECTION LIMIT 0` i kończy istniejące sesje. Blokuje
to również nowe połączenia ról `ai_app` i `mlflow_app` z jednorazowych
kontenerów operatora. Kopię wykonuje lokalny administrator, który może
łączyć się mimo limitu; dump/restore przechodzą na właściwą rolę aplikacji
przez `--role`. Administrator musi powstrzymać własne zapisy i zmiany
konfiguracji na czas operacji. Blokada nie jest ochroną przed superuserem
ani bezpośrednią administracyjną modyfikacją wolumenu.

Pakiet pod `.local/lifecycle-backups/` zawiera:

- `ai.dump`: pełny schemat aplikacji `ai`, w tym audyt modelu i dane RAG;
- `mlflow.dump`: schemat `public` metadanych MLflow;
- `artifacts.tar`: wszystkie pliki wolumenu MLflow;
- `state.json`: liczby i SHA-256 posortowanych obrazów wierszy każdej tabeli,
  wartości sekwencji i rewizję migracji AI;
- `manifest.json`: identity, checksumy/rozmiary, czas UTC i piny Compose,
  obu Dockerfile, projektu/locka zależności i kodu/kontraktów aplikacji,
  w tym migracji.

Źródło z tabelami w innych schematach jest odrzucane, aby nie tworzyć
niepełnego backupu. Role, hasła i rozszerzenie pgvector powstają przez
istniejący `init.sh` celu; poświadczenia nie trafiają do pakietu.
Plikowe snapshoty/curated i artefakty poza wolumenem MLflow nie należą do
tej kopii. Chroni ona obecny stan aplikacji w PostgreSQL i magazynie MLflow,
nie stan całego hosta lub przyszłego worker storage.

Katalogi pakietów mają 0700, pliki 0600; publikacja pakietu jest atomowa
i nie nadpisuje istniejącej kopii. Kontroler serializuje własne operacje.
Sprawdza stan tabel także po dumpach, zanim opublikuje pakiet. Po sukcesie
lub zwykłym błędzie przywraca wcześniejsze limity i tylko wcześniej
działające usługi. Identity i checksumy wykrywają zmianę zawartości; nie
są podpisem kryptograficznym dostawcy. Odtwarzaj wyłącznie zaufane kopie.

## Przerwany backup

Po SIGKILL lub utracie hosta źródło może pozostać zatrzymane i z limitem 0.
Nowy backup nie usuwa tego stanu automatycznie. Sprawdź dziennik maintenance
i stan bazy, następnie wykonaj:

```bash
.venv/bin/python scripts/lifecycle_store.py resume
```

`resume` wymaga zgodnego identyfikatora klastra i oczekiwanych limitów;
odmawia odblokowania odtworzonego pod tą samą nazwą, innego klastra lub
nieoczekiwanego stanu administracyjnego. Zachowuje dziennik, jeśli
przywrócenie usług się nie powiedzie, więc ponowienie pozostaje możliwe.
Nie usuwaj ręcznie dziennika jako sposobu wznowienia.

## Pusty cel i kontrola odtworzenia

`verify` działa bez Dockera i sprawdza inventory, identity, oba dumpy,
checksumy, stan tabel oraz bezpieczne nazwy i limity archiwum.
Limity lokalne to milion wierszy w inventory, 256 tabel/sekwencji na bazę,
100 000 plików artefaktów i 4 GiB na plik pakietu/treść artefaktów.

`restore` odmawia użycia projektu źródłowego, bieżącego checkoutu lub celu
z istniejącymi kontenerami, siecią albo wolumenami, także konfliktującymi
wolumenami bez etykiety Compose. Wymaga zgodnych pinów konfiguracji.
Starszą kopię odtwarzaj z przypiętej rewizji albo poprzez osobną procedurę
migracji; restore nie uruchamia nowej migracji nad odtwarzanym evidence.

Nowy cel pozostaje zablokowany podczas odtwarzania obu baz i artefaktów.
Każdy dump jest odtwarzany transakcyjnie. Po transferze kontroler ponownie
oblicza wszystkie checksumy wierszy/sekwencji i eksportuje cały wolumen,
aby sprawdzić bajty również nieaktywnych artefaktów. Dopiero pełna zgodność
odblokowuje bazy. Serwery pozostają zatrzymane do jawnego uruchomienia.
Nie ma jednej transakcji obejmującej obie bazy i wolumen: częściowo
odtworzony cel pozostaje offline, a ponowienie wymaga kolejnego nowego
projektu. Nie uruchamiaj usług na niezweryfikowanym celu.

Po udanym restore, w katalogu checkoutu zgodnego z pinami, zwolnij lokalne
porty przez zatrzymanie źródłowego stosu i jawnie uruchom nowy cel:

```bash
make compose-down
docker compose -p retailops_ai_<nowy_projekt> \
  --env-file .local/compose.env -f compose.yaml up -d --wait api mlflow
```

`compose-down` zachowuje źródłowe wolumeny. Nie dopisuj `--volumes`.

Odtworzenie zachowuje także niedokończone decyzje. Wznów je istniejącym
decision ID, zgodnie z [recovery lifecycle](mlflow-lifecycle.md), po
odczytaniu rzeczywistego Registry. Sam restore nie zatwierdza modelu,
nie zmienia aliasów i nie przyznaje jakości brakującym dowodom. Wygasła
kwalifikacja może nadal blokować późniejszą promocję lub load.

## Odbiór i granice

[Odbiór 05.3c](evidence/05-03-store.md) używa nowych, jednorazowych projektów.
Sprawdza SIGKILL kontrolera, odmowę nowych połączeń aplikacji, wznowienie,
cztery kapsuły modelu, zachowanie championa/rollback/rejection oraz
recovery rejestracji po utracie odpowiedzi, także po SIGKILL odtworzonego
stosu. Required CI wykonuje `lifecycle-store-smoke` w jobie `persistence`.
Testowe API/MLflow nie zajmują portów hosta i nie zatrzymują stosów innych
checkoutów; są dostępne tylko w swojej sieci Compose.

Pakiety nie trafiają do Git i nie mają automatycznej retencji. Zasady
zachowania aktywnych/rollback artefaktów opisuje [magazyn MLflow](mlflow-store.md).
Kopia poza hostem, szyfrowanie i harmonogram pozostają decyzją przed
produkcją. Ten odbiór jest lokalny i dotyczy mechaniki; AI 05 nadal wymaga
kwalifikowanego modelu AI 04 oraz batch/runtime/API z kolejnych zakresów.
