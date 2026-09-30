# AI 05.3b — odbiór mechaniki Registry i recovery

Wynik lokalnego testu rzeczywistego PostgreSQL i MLflow: **passed**.
[Raport JSON](05-03-lifecycle.json) zawiera identyfikatory runów,
checksumy kwalifikacji, release i projekt jednorazowy.

Wykonano `make model-lifecycle-smoke` z przypiętym lokalnym UV. Test tworzy
odizolowany Compose project, migruje bazę AI do `0009_model_lifecycle`,
zapisuje i ładuje trzy działające modele JSON w osobnej nazwie
`retailops-demand-forecast-mechanics`. Ścieżka używa rzeczywistego
uwierzytelnienia prywatnego operatora i roli `promoter`.

Sprawdzono rejestrację, pierwszy release z previous_version=null, dwie
promocje, dokładne odtworzenie przypięć wcześniejszego release’u i
odrzucenie trzeciego kandydata bez zmiany championa. Wymuszono utratę
odpowiedzi po create i po zmianie aliasu; wznowienie nie utworzyło dodatkowej
wersji i zachowało wcześniejszy zatwierdzony pointer do końca operacji.
Replay dawnej promocji nie cofnął nowszej decyzji.

Po SIGKILL i restarcie PostgreSQL/MLflow zachowały się decyzje, wersje,
checksumy artefaktów i przypięty release. Trigger zabronił zmiany historii.
Projekt, sieć i wolumeny testowe zostały usunięte przez test; nie ma tych
wersji w właściwym forecast Registry. Model AI 04 nie został promowany.

To dowód mechaniki, **nie** portfolio model-quality gate ani udanego
persisted forecast/API. Worker/runtime pozostają `not_integrated`.
Wspólny backup/restore niezależnego audytu AI i MLflow ma osobny
[odbiór 05.3c](05-03-store.md).
[Runbook i braki](../mlflow-lifecycle.md) opisują dokładną granicę odbioru.

## Weryfikacja repozytorium

Pełny zestaw: **963 passed** (618,30 s). Po ujednoliceniu prywatnej
weryfikacji operatora dodatkowo **75 passed** w testach access, Registry
oraz lifecycle. Ruff, format, mypy (174 pliki), dokumentacja, wszystkie
checkery snapshot/curated/forecast, kontrakty i wheel/sdist przechodzą.

`make check` wykrył cztery stare schematy knowledge pomijające rolę
`promoter`, wprowadzoną wcześniej w 05.3a. Regeneracja zmieniła wyłącznie
ich enum ról; ponowne `make contracts-check package compose-config`
zakończyło się poprawnie. Nie zmieniono etykiet golden setu ani bramek jakości.

`make compose-smoke` przeszedł na rewizji `0009_model_lifecycle`, z
`/health=200` i `/ready=200` po recovery. `make model-lifecycle-smoke`
potwierdził również blokadę między niezależnymi sesjami operatora.
Gitleaks katalogu i historii Git oraz `git diff --check` nie wykryły problemów.
Zdalny Required CI nie był uruchamiany; nie wykonano push.
