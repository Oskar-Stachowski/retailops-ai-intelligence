# AI 05.3c — odbiór wspólnego backup/restore

Wynik lokalnego testu rzeczywistego PostgreSQL i MLflow: **passed**.
[Raport JSON](05-03-store.json) wskazuje backup ID, przypięty release,
checksumy/rozmiary wszystkich plików oraz hashe konfiguracji i inventory.
[Runbook](../lifecycle-backup.md) opisuje rzeczywiste polecenia i recovery.

Wykonano `.venv/bin/python scripts/check_lifecycle_store.py`, czyli odbiór
udostępniony przez `make lifecycle-store-smoke`. Powstały dwa nowe projekty
Compose: źródło oraz pusty cel. API/MLflow są dostępne wyłącznie wewnątrz
ich sieci; test nie zajmuje portów hosta i nie zatrzymuje innych checkoutów.
Oba projekty, sieci i wolumeny testowe są usuwane po odbiorze. Prywatny
pakiet backupu pozostaje lokalnie, poza Git.

Źródło ma rewizję `0009_model_lifecycle` i trzy zarejestrowane modele
mechaniczne z audytem dwóch promocji, rollbacku i odrzucenia. Czwarta
rejestracja celowo traci odpowiedź po rzeczywistym utworzeniu wersji w
MLflow, pozostawiając niezakończony request i zamiar create w bazie AI.
Nie zmieniono właściwego forecast Registry ani bramek jakości AI 04.

## Co sprawdzono

- SIGKILL kontrolera po zatwierdzeniu blokady pozostawia obie bazy z
  limitem połączeń 0 i prywatnym dziennikiem maintenance. Obie role
  aplikacji rzeczywiście nie mogą otworzyć nowej sesji. Jawne wznowienie
  przywraca wcześniejsze usługi oraz zgodny audyt/Registry.
- Backup kopiuje oba schematy aplikacji i wszystkie 60 plików artefaktów,
  z SHA-256, identyfikatorem treści i prywatnymi uprawnieniami plików.
  Stan źródła jest odczytany przed i po dumpach w czasie blokady zapisów.
- Restore do nowego projektu odtwarza wszystkie wiersze każdej tabeli,
  wartości sekwencji i pełny wolumen. Ponowny eksport ma identyczne bajty
  i checksum archiwum, także dla nieaktywnych wersji. API/MLflow pozostają
  zatrzymane do zakończenia weryfikacji i jawnego uruchomienia.
- Drugie restore do tego samego celu jest odrzucane przed nadpisaniem.
  Błąd częściowego restore zachowuje blokadę połączeń i nie uruchamia usług.
- Odtworzona niedokończona rejestracja wznawia się z tym samym decision ID
  i istniejącą wersją 4. Powtórzenie nie tworzy wersji 5, nie zmienia
  zatwierdzonego release’u ani wcześniejszego odrzucenia wersji 3.
- Wszystkie cztery odtworzone kapsuły przechodzą rzeczywisty odczyt,
  checksum i load/inference smoke. Trigger bazy nadal blokuje zmianę
  historycznej decyzji. Po SIGKILL/restart odtworzonego stosu API wraca
  do gotowości; ponowny odczyt i replay zachowują stan.

Testy negatywne obejmują uszkodzenie każdego pliku pakietu, niebezpieczne
nazwy/symlink w archiwum, dodatkowe pliki, konfliktujące kontenery/sieci/
wolumeny, niezgodną konfigurację, odtwarzanie do źródła, częściowy restore,
równoległy kontroler oraz próbę odblokowania innego klastra lub
nieoczekiwanych limitów. Required CI wymaga `lifecycle-store-smoke`
w jobie `persistence`; test workflow odrzuca usunięcie tej bramki.

## Weryfikacja repozytorium

Pełne `.venv/bin/python -m pytest -q --junitxml=reports/ai05-store-tests.xml`
zakończyło się wynikiem **988 passed** (1116,84 s). Finalne testy backupu,
odtwarzania i guardów CI: **48 passed**. Ruff, format (300 plików),
mypy (177 plików), linki dokumentacji, kontrakty, wheel/sdist i konfiguracja
Compose przechodzą. Pakiet wskazany w raporcie został dodatkowo zweryfikowany
offline ze zgodnymi pinami bieżącego kodu.

Gitleaks katalogu i historii Git oraz `git diff --check` przechodzą.
Skaner początkowo rozpoznał hash `Dockerfile.api` jako generic API key.
Hash porównano z rzeczywistym plikiem; wyjątek dotyczy wyłącznie tej
konkretnej sumy w `docs/evidence/05-03-store.json`, bez wyłączania reguły.

## Granice

Odbiór dotyczy spójnego stanu w czasie przerwy administracyjnej, także
z decyzją wymagającą recovery. Nie jest transakcją rozproszoną pomiędzy
Registry, niezależnym audytem i artefaktami. Superuser oraz administrator
wolumenu mogą ominąć blokadę; podczas backupu nie mogą wykonywać zapisów.
Retencja, szyfrowana kopia poza hostem i automatyczny harmonogram nie są
częścią tego odbioru.

Modele są wyłącznie `lifecycle_mechanics_only`; quality AI 04 nie zostało
zatwierdzone, a runtime pozostaje `not_integrated`. Batch i read API
prognoz należą do następnych zakresów. AI 05.3 wymaga jeszcze odbioru
rzeczywistego kwalifikowanego modelu. Nie wykonano push ani zdalnego
Required CI.
