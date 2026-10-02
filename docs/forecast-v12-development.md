# AI 05 — lokalny odbiór zaakceptowanego v12

Decyzja właściciela z [AI 04](evidence/04-v12-acceptance.json) przyjmuje
konkretny v12 jako końcowy wynik etapu developerskiego. Trzy niezaliczone
kontrole MSE i oryginalne `forecast_model_status=not_ready` pozostają bez zmian.
Decyzja nie dopuszcza produkcji ani automatycznej promocji w registry.

AI 05 może wykonać odrębny, lokalny odbiór ładowania i prognozowania tego
pakietu. Opcja `--development-acceptance docs/evidence/04-v12-acceptance.json`
w `scripts/mlflow_v12_release.py qualify` sprawdza dokładny hash i rozmiar
decyzji, run ID oraz hash całego manifestu. Nie przyjmuje innego raportu,
innej kampanii ani ogólnego wyłączenia bramek.

Pełny verifier AI 04, pochodzenie źródła i cech, PIT, sygnatura, identyczne
wyniki dwóch prób, limity czasu/pamięci i wszystkie dziesięć raportów przeglądu
pozostają wymagane. `segments` może dokumentować przyjęcie trzech wskazanych
odstępstw wyłącznie w tym zakresie; nie oznacza poprawienia historycznych MSE.
Świeżość wyniku nadal wynika z dat źródła i origin, a nie czasu jego importu.

Potwierdzony w kalendarzu zamknięty dzień pozostaje częścią kompletnego batcha.
Oryginalny predictor v12 zwraca dla niego `null` dla mediany, średniej i
przedziału; adapter dodaje `exclusion_reason=closed_target`. Nie wpisuje zera
i nie pomija wiersza. Nieznany lub sprzeczny kalendarz nadal blokuje wykonanie.

## Osobna przestrzeń i decyzje

Takie dopuszczenie wymaga modelu `retailops-demand-forecast-v12-development`.
Nie można zarejestrować go pod domyślnym `retailops-demand-forecast-v12` ani
testowym `retailops-demand-forecast-v12-mechanics`. Zwykły model nadal wymaga
oryginalnego `ready/passed`. Migracja `0019_v12_development` sprawdza tę samą
granicę również w bazie, zachowując wcześniejsze kontrole transakcji i audytu.

Operator nadal uwierzytelnia oddzielny przegląd, rejestrację oraz każdą decyzję
promocji/odrzucenia/rollback. Sam wybór trybu developerskiego niczego nie
promuje. Prywatne narzędzia kolejki, workera i importu ocen przyjmują jawne
`--development`; upload i review registry przyjmują dokładną nazwę modelu.

API udostępnia tę przestrzeń tylko po ustawieniu `V12_DEVELOPMENT_MODE=true`
w oddzielnej instancji `APP_ENV=local` lub `test`. Domyślne API, reader, katalog
i oceny nie pokazują jej danych. Tokeny, scope, paginacja i kontrola jakości
zapisanych wyników działają również w instancji developerskiej.

## Import przy ograniczonym miejscu na macOS

Pełny eksport ma około 30 GiB. Domyślny import strumieniowy przed nowym runem
sprawdza, czy po zapisie całej kampanii zostanie `--minimum-free-gib` (domyślnie
50 GiB). Powtórzenie już ukończonego importu tylko weryfikuje istniejące pliki
i nie wymaga miejsca na drugą kopię.

Na macOS można jawnie wskazać prywatny katalog APFS, który jest jednocześnie
artifact store osobnego MLflow przez bind mount:

```bash
.venv/bin/python scripts/mlflow_v12_evidence.py \
  --run-dir /private/path/completed-v12-export \
  --verifier-python /private/path/pinned-ai04-venv/bin/python \
  --mlflow-port LOCAL_PORT \
  --clone-artifact-root /private/path/host-mounted-artifact-store \
  --minimum-free-gib 50
```

Ten tryb wymaga właściciela operatora, katalogów 0700, tej samej partycji APFS
i jawnej zgodności mountu z serwerem. Tworzy osobne pliki przez `fclonefileat`;
zapis w jednym pliku nie zmienia oryginału. Nie przechodzi automatycznie do
kopiowania ani hardlinku oryginału. Sprawdza SHA i rozmiar obu plików, a potem
bajty udostępnione przez HTTP MLflow. Odmowa klonowania lub naruszenie rezerwy
kończy import błędem. Częściowy import zachowuje dotychczasowe reguły `FAILED`.

Jest to jawny wariant lokalnego artifact store na hoście; podstawowy Compose
zachowuje named volume. Klon APFS nie jest niezależnym backupem. Utrata tego
samego dysku może usunąć obie kopie. Przed użyciem produkcyjnym potrzebne są
odrębna decyzja, przegląd aktualności źródła i backup poza tym dyskiem.

## Dowody

Testy przenośne używają jawnych małych doubles. Odbiór bazy z rzeczywistymi
PostgreSQL i MLflow sprawdza również dokładną decyzję developerską, odmowę
innego runu i zachowanie wszystkich pozostałych bramek. Test backup/restore
pozostaje odbiorem mechaniki na fixtures; nie oznacza backupu całej kampanii.
Rzeczywisty import i ścieżka prognozy mają
[osobny raport lokalnego odbioru](evidence/05-v12-real-serving.md).
