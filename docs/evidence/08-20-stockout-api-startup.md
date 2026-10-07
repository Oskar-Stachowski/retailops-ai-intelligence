# AI 08.20 — start API i pełna kontrola typów

CI przyrostu 08.19 wykryło błąd typów w generatorze schematów. Wcześniejsze
lokalne `mypy src` pomijało skrypty; jego sukces nie oznaczał pełnego Mypy.
Poprawka jawnie typuje mapę modeli i przechodzi skonfigurowane Mypy dla `src`
i `scripts`. Historyczne evidence 08.19 zachowuje wcześniejszy zakres.

Pętla rzeczywistego HTTP nie doczekała startu serwera. Publiczne modele,
porty i błędy stockout są teraz niezależne od prywatnego lifecycle, przygotowania
i treningu. Adaptery SQL tworzą się przy użyciu przez uprawnione żądanie.
Osobny zimny proces buduje API z konfiguracją bazy i OpenAPI przy zablokowanym
imporcie tych modułów. 186 testów API, scope, modeli i kolejki przechodzi. Dotychczasowe JSON/OpenAPI
nie zmieniają się, a limit
startu loopback pozostaje bez zmian. Potwierdzenie naprawy w rzeczywistym
Compose, nowego SQL 0021 oraz pełnego backup/restore nadal wymaga nowego CI.

Trzy matching i trzy późniejsze źródła na seedach 42/137/2026 zakończyły
przygotowanie. Publiczne receipts i sumy ZIP sprawdzono. Końcowych wyników
modelu nie otwarto; prywatne dane nie zostały odtworzone lokalnie.

Poprzedni [pełny CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37246436824)
zaliczył 2383 testy, SQL 0020, persistence i backup. [Nowy niezaliczony CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37250135424)
nie jest odbiorem SQL 0021. **Cały AI 08 pozostaje not ready.**
