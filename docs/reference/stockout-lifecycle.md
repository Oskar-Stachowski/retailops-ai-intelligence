# Stockout — wspólny mechanizm wersjonowania AI 05

Aktualny [pełny rzeczywisty odbiór](../evidence/08-27-final-serving-acceptance.md)
zaliczył kwalifikowany model, 12 bramek review, pełny verifier/importer MLflow,
register/promote/rollback/reject oraz durable cold worker i scoped batch/API.
Poniższy opis otwartych prac i fixture dotyczy historycznego przyrostu 08.18.
Właściwy model jest rozdzielony od fixture mechanics; odbiór był izolowany i
nie uruchomił produkcyjnego wdrożenia.


`StockoutLifecycle` używa tego samego odzyskiwalnego protokołu co adapter v12.
Kod wspólny to `model_lifecycle/reviewed_engine.py`; typed request, approval,
binding oraz release pozostają osobnymi kontraktami każdego modelu. Regresja
dotychczasowego v12 przechodzi. To podstawa integracji, nie zakończone serving.

## Zatwierdzenie i izolacja

Stockout ma namespace `retailops-stockout-risk`. Mały fixture techniczny używa
wyłącznie `retailops-stockout-risk-test-mechanics` i środowiska `test`. Nie może
zostać przeklasyfikowany jako produkcja. Produkcyjna kwalifikacja wymaga receipt
niezależnej końcowej jakości i przypiętej kampanii. Kwalifikacja wiąże pełną
recepturę modelu/kalibratora, politykę, kartę, wejścia publiczne i smoke.

Approval wymaga dokładnie 12 zaliczonych bramek: source, features, pit, protocol,
segments, signature, resources, security_license, model_card,
freshness_drift_compatibility, calibration i threshold_capacity. Zawiera
przejrzany image digest i tożsamość promotera. Sama poprawność typed JSON nie
potwierdza prawdziwości receipt; rzeczywisty verifier pakietu i rejestr MLflow
stockout pozostają do zintegrowania. Żadnego produkcyjnego approval nie utworzono.

## Decyzje i odzyskiwanie

Decyzje register/reject/promote/rollback wymagają promotera i `model:decide`.
Idempotencja obejmuje treść oraz principal. Wspólna blokada advisory PostgreSQL
serializuje decyzje w namespace. Niedokończona decyzja wymaga wznowienia.
Intent jest utrwalony przed efektem w zewnętrznym rejestrze.

Po utracie odpowiedzi tworzenia wersji można odnaleźć jedną dokładną wersję
z tą samą decyzją. Nieznany wynik nie powoduje ponownego POST. Alias może być
wyłącznie w dozwolonym stanie przed/po konkretnej decyzji; obcy stan blokuje
odzyskiwanie. Promocja wymaga nowego candidate oraz dokładnego przejrzanego
obrazu. Reject nie może usunąć champion/rollback. Rollback odtwarza dokładny
poprzedni release, łącznie z approval, modelem, kalibratorem, polityką i obrazem.
Ponowienie starej zakończonej decyzji nie cofa bieżącego head.

Migracja `0020_stockout_lifecycle`, następująca po `0019_v12_development`, dodaje
osobne append-only tabele decisions/steps/versions/releases/heads. Triggery
sprawdzają decyzję rejestracji, pełny binding release, poprzedni head oraz
ukończenie decyzji. Release/head/completed są jedną transakcją; deferred guard
odmawia nawet bezpośredniemu pisarzowi SQL zatwierdzenia head bez completion.
Historia jest niemodyfikowalna. Downgrade wymaga backup/restore.

## Odbiór

23 nowe testy mechaniki stockout oraz 79 istniejących testów lifecycle,
publikacji, kolejki i API v12 przeszło. Dodatkowe 35 testów lifecycle-store
i readiness przechodzi przy wymaganym nowym head. Mypy/Ruff są zaliczone.

`tests/check_stockout_lifecycle.py` jest wywoływany przez istniejący prywatny
runner AI 05. Sprawdza realny PostgreSQL, transakcje, SQL guards, wznowienie,
rollback, restart i stan po odtworzeniu całego backupu. Stockout registry jest
w tym teście jawnym double; test nie potwierdza prawdziwego MLflow stockout.
Nowa rzeczywista akceptacja SQL/backup jest jeszcze oczekująca w CI.
Lokalnej bazy źródła lub drugiej sesji nie migrowano i nie zmieniano.

MLflow stockout, verifier prawdziwego finalnego pakietu, durable batch,
publikacja/odczyt ryzyka i finalna kwalifikacja pozostają otwarte. Wynik
protokołu nadal ma `runtime_status=not_integrated`. **Cały AI 08 nie jest ready.**
