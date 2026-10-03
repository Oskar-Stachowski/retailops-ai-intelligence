# AI 05.5b — trwałe wejścia i kontrolowany preflight

Ten zakres dodaje prywatny rejestr prepared inputs w PostgreSQL oraz
supervisor dla loadera AI 05.5a. [Odbiór](evidence/05-05-input-store.md)
sprawdza rzeczywisty pakiet, współbieżny zapis, rollback i SIGKILL/restart.
[AI 05.6](forecast-publication.md) łączy ten rejestr i supervisor z kolejką
i atomowym outputem. Serving wymaga zakwalifikowanego modelu.

## Trwała rejestracja

Migracja `0011_forecast_inputs` tworzy `ai.forecast_prepared_inputs`.
Stos trzeba zmigrować jawnie; API nie wykonuje migracji przy starcie.
Rejestr jest oddzielony od fixture `forecast_batch_profiles` i od prognoz.
`local` oraz `test` mają niezależne klucze `(environment, profile_id)`.

Rejestracja ponownie waliduje cały typed pakiet, identity, grain/historię
i 32 MiB canonical JSON. Baza utrwala cały profil, jego canonical SHA,
rzeczywisty rozmiar JSONB i własny timestamp. Odczyt ponownie sprawdza treść
i checksum; origin nie może być późniejszy niż chwila rejestracji.
Update i delete profilu lub receipt są blokowane przez trigger, również
gdy zapis pozostawiłby takie same wartości.

Powtórzenie zwraca ten sam profil, checksum i czas, także po osiągnięciu
limitu. Advisory lock chroni równoczesne rejestracje. Limit dla środowiska
to **256 profili i 256 MiB JSONB**; jest sprawdzany również przez trigger
w bazie. Prywatny proces może przyjąć mniejszy budżet, bez zwiększenia tych
limitów. Post-insert kontrola rzeczywistego rozmiaru działa w tej samej
transakcji: przekroczenie budżetu cofa cały insert. Baza ogranicza pojedynczy
JSONB do 64 MiB; limit aplikacyjnego canonical pakietu pozostaje 32 MiB.

Zaufany proces z settings i `DATABASE_URL` wykonuje:

```bash
python -m retailops_ai.forecast_jobs.runtime_cli inputs-register \
  --inputs-dir /private/path/batch-profile-sha256-<64-hex>
```

Profile pochodzą z [przygotowania zweryfikowanych features/curated](forecast-runtime.md).
To prywatna operacja z poświadczeniami bazy, bez publicznego uploadu,
nadawania grantów ani zatwierdzania jakości. Receipt ma cel
`verified_inputs_only`. Brak miejsca wymaga przeglądu retencji danych;
nie usuwamy wpisów automatycznie. Backup/restore schematu `ai` obejmuje
nową tabelę; downgrade wymaga [procedury odtworzenia](lifecycle-backup.md).

## Supervisor i worker

Prywatny worker pobiera profil po ID z rejestru swojego środowiska,
a dokładny release z niezależnego audytu PostgreSQL. Nie przyjmuje ścieżki
ani model URL od klienta i nie rozwiązuje ponownie aliasu.

```bash
python -m retailops_ai.forecast_jobs.worker --preflight \
  --profile-id batch-profile-sha256-<64-hex> \
  --release-id model-release-sha256-<64-hex>
```

Wymagane są `DATABASE_URL`, rzeczywisty `IMAGE_DIGEST` i pozostałe settings
procesu. Dotychczasowe `runtime_cli release-check` na prywatnym pliku również
używa tego supervisora. Wykonanie wymaga qualified release z wszystkimi
bramkami `passed` oraz zgodnych pinów image, lock i wejścia. Numeric child
sprawdza też lock faktycznie zainstalowanego pakietu, a loader ponownie
weryfikuje Registry, config/signature i dokładnie załadowane bajty modelu.

Obliczenia działają w osobnej sesji procesów i izolowanym interpreterze
`python -I`. Child nie dziedziczy DB/API/AWS/MLflow credentials, proxy ani
`PYTHONPATH`; dostaje tylko prywatny JSON, ustawienia locale/path i jawny
limit wątków numerycznych do jednego. Nie ma dostępu do kolejki, jej lease
tokenu ani publikacji wyników.

Domyślne limity: **120 s wall time, 60 s CPU, 1024 MiB RSS**.
Supervisor sprawdza RSS drzewa procesów, czas i rozmiar stdout/stderr;
child ma kernel CPU/file caps i własny alarm. Linux ma dodatkowo limit
2 GiB przestrzeni adresowej. macOS odrzuca `RLIMIT_AS`, dlatego stosuje
guard RSS supervisora oraz kontrolę rzeczywistego peak po obliczeniu.
Brak dostępu do metryk pamięci kończy próbę; nie pomijamy limitu.
Nie jest to przydział zasobów wspólny dla wielu workerów ani cgroup quota.

Payload ma najwyżej 34 MiB, wynik 256 KiB, stderr 16 KiB. Prywatne pliki
mają 0600 i katalog 0700; są sprzątane przy zakończeniu próby. Po SIGKILL
supervisora może pozostać prywatny staging; przed jego usunięciem trzeba
potwierdzić zakończenie procesów tej próby. Timeout, błąd lub odmowa
callbacku lease kończą własny proces obliczeń i czekają na jego zakończenie.
Kernel alarm ogranicza również pracę childa po SIGKILL supervisora.
Hook heartbeat/lease jest podłączony do przyjętego runu i transakcji
publikacji w [AI 05.6](forecast-publication.md).

Wynik `runtime_preflight_only` jest typowany, przypina profile/release ID,
liczbę i hash skończonych, nieujemnych wartości oraz czas/peak RSS.
CLI zwraca wyłącznie metadane, bez wartości prognoz i szczegółów błędów
z poświadczeniami. Nie tworzy output artifact, runu `succeeded` ani nowej
wersji/promocji modelu.

## Odbiór i pozostały zakres

```bash
make forecast-input-store-smoke
```

Required CI uruchamia tę bramkę na kontrolowanym typed fixture, w świeżym
PostgreSQL i bez portów hosta. Prywatny odbiór pełnego 14-dniowego pakietu:

```bash
python scripts/check_forecast_input_store.py \
  --inputs-dir /private/path/batch-profile-sha256-<64-hex> \
  --report reports/forecast-input-store-private.json
```

Kontroler usuwa tylko swój projekt i wolumen. Udana rejestracja nie otwiera
servingu: archiwum 04.8 pozostaje `not_ready` i ma starszy lock features.
Przed odbiorem qualified execution trzeba przygotować spójny, zakwalifikowany
pakiet AI 04. [AI 05.6](forecast-publication.md) ma oddzielny odbiór
techniczny przyjęcia, fencing i transakcji całego wyniku na PostgreSQL.
[AI 05.7a](forecast-read.md) dodaje scoped odczyt prognoz z paginacją i oceną freshness;
[katalog 05.7b](model-catalog.md), [oceny 05.7c](evaluations.md) oraz
[watermark 05.7d](forecast-freshness.md) mają osobne odbiory.
Pozostaje qualified batch AI 04. Zdarzenia należą do AI 10.
AI 05 pozostaje otwarte; zdalny Required CI tego brancha nie został odebrany.
