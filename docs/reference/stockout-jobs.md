# Zadania i odczyt ryzyka stockout

`stockout_jobs` dodaje osobną trwałą kolejkę fizycznych par produkt/magazyn.
Korzysta z limitów, zegara PostgreSQL, leases, retry i append-only historii AI 05.
Nie fituje modeli ani nie generuje źródeł.

POST `/api/v1/stockout-runs` wymaga pipeline, `stockout:run`, jawnego stockout
scope i pojedynczego Idempotency-Key. Klient podaje istniejący ID przygotowanych
publicznych wejść, origin i scope. Nie przesyła features, prywatnych ścieżek lub
kanałów. Wewnętrzna rejestracja odtwarza pełne publiczne curated/features/upstream;
nie ma HTTP przyjmującego arbitralne wiersze. Limit: 20 produktów × 5 magazynów,
16 MiB/pakiet i 32 pakiety / 128 MiB w rejestrze.

Przyjęcie wiąże cały release: model, kalibrator, progi, approval i image digest.
Wymaga aktualnej zgody, zgodnego origin i całego scope. Niedokończona decyzja
lifecycle blokuje przyjęcie. Idempotencja wiąże principal, środowisko, klucz
i semantyczną treść żądania. 202/Location pojawia się dopiero po commit backendu.

Kolejka ma deadline, niejawny UUID lease i maksymalnie 5 prób. Heartbeat
i publikacja sprawdzają własność próby oraz ważność modelu. Stary worker nie
może opublikować po utracie lease lub rozpoczęciu nowej próby. Retry następuje
po dopisaniu zamkniętej historii. `verify_output` powtarza pełny wynik z tych
samych wejść i pinów; odmawia resealed zmienionego prawdopodobieństwa.
Output, succeeded i historia są jedną transakcją. Deferred SQL guard blokuje
także bezpośredni sukces bez kompletnego wyniku i historii.

Migracja `0021_stockout_jobs` po `0020_stockout_lifecycle` dodaje immutable
prepared inputs, trwałe runs, attempts i outputs. Readiness wymaga head 0021.
Lokalnych baz źródła i innych sesji nie migrowano. Downgrade wymaga backup/restore.

GET runs/attempts pokazuje publiczny pin zamiast pakietu approval. GET
`/api/v1/stockout-risks` i pojedynczego risk wymaga `stockout:read` i fizycznego
scope. Całe stored outputs są weryfikowane przed projekcją do dozwolonych
wierszy. Najnowszy wynik jest wybierany osobno dla produktu/magazynu.
Origin i inference run mogą zawęzić odczyt. Limit: 32 outputs, 4 MiB odpowiedzi;
większa historia zwraca błąd budżetu zamiast niejawnego ucięcia. Dalsza strona
wymaga SHA256 tego samego widoku; zmiana daje 409.

Stan zapasu w dniu prognozy jest oddzielony od aktualności wyniku dzisiaj.
Origin starszy niż 24 h lub nowsze nieopublikowane zadanie oznacza stale.
Brak globalnego watermark daje unknown. Odczyt zachowuje prawdopodobieństwo,
pierwotny czas i fizyczne przyczyny statusu.

163 testy uprawnień/API/wejść/inferencji/lifecycle/readiness/store oraz 36 testów
HTTP przechodzą. Ruff/format, Mypy i schematy są zaliczone.
`tests/check_stockout_jobs.py` rozszerza własny disposable runner AI 05 o
prawdziwy PostgreSQL, fences, atomową publikację, scope, retry, restart i pełny
backup/restore. Nowy SQL 0021 oczekuje w CI. Poprzedni SQL 0020 i backup są
zaliczone na 7ac8ab6. Fixture SQL jawnie używa syntetycznych inputs i namespace
testowego; nie potwierdza jakości lub kwalifikacji produkcyjnego źródła.

Rzeczywisty MLflow, verifier finalnego pakietu, prywatny worker, zatwierdzona
polityka, końcowa kampania i odbiór całego etapu są otwarte. **AI 08 nie jest ready.**
