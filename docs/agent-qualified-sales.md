# Sprzedaż AI12 z kwalifikowanych dni

`QualifiedSalesReader` udostępnia obserwowaną sprzedaż z istniejącego,
niezależnie weryfikowanego łańcucha Source → Curated → pełny Raw DQ → deklaracje
zamknięcia dni. `QualifiedSalesTool` podłącza ten odczyt do `get_sales_summary`.
Nie eksportuje Source, nie wykonuje treningu ani inference i nie zmienia
rodziców, baz, rejestru lub strumieni.

```python
from retailops_ai.adapters.qualified_sales_tool import QualifiedSalesReader, QualifiedSalesTool

reader = QualifiedSalesReader(replay_dir, coverage_dir, curated_dir, import_dir, "local")
adapters["get_sales_summary"] = QualifiedSalesTool(reader, "local")
```

Ścieżki i środowisko należą do konfiguracji serwera. Reader należy utworzyć
przy starcie, przed przyjmowaniem zapytań: istniejący weryfikator rodziców
odtwarza źródłowe fakty, Curated, przyjęte zdarzenia DQ i deklaracje zamknięcia.
Zachowuje ich prywatną kopię w pamięci. Kolejne odczyty pytają `DayGate`
o wiedzę dostępną dokładnie w `request.as_of`, bez ponownej rekonstrukcji
rodziców. Ponowne uruchomienie lub podłączenie nowego źródła wymaga weryfikacji.
Utworzenie readera nie kwalifikuje trwałości transportu ani gotowości modeli.

Adapter wymaga roli `operator`, `assistant:query`, `sales:read` i całego
żądanego zakresu sprzedaży. Przekazuje tę samą tożsamość z zakresem zawężonym
do dokładnych produktów, lokalizacji sprzedaży i kanału. Fizyczny magazyn
nie zastępuje lokalizacji sprzedaży. Środowiska `local` i `test` muszą zgadzać
się przy konstrukcji adaptera i przy każdym wykonaniu.

Niepusty wynik wymaga potwierdzenia każdego dnia dla każdej kombinacji produktu,
lokalizacji i kanału. `no_declaration`, `closure_unavailable`, `source_incomplete`,
`location_closed`, `dq_unattributed_quarantine` lub `dq_missing_facts` wstrzymują
cały okres. Wynik zachowuje wszystkie sprawdzone punkty i ich statusy, lecz
nie zwraca częściowej sumy ani nie uzupełnia braków zerami. Jawne zero jest
dopuszczone wyłącznie z pełnego okresu o statusie `qualified`.

Suma dotyczy obserwowanej sprzedaży w sztukach, bez odejmowania zwrotów,
wnioskowania nieocenzurowanego popytu lub przeliczania walut. Niejednoznaczna
waluta w jednej serii, powtórzony grain, obcy zakres, niewłaściwy cutoff,
niepełna siatka dni lub nieprecyzyjna konwersja sumy do typu jednostek
są odrzucane.

Istniejący `SalesResult` zyskuje opcjonalne `qualified_days` z dokładnym
żądaniem, dziennymi `Point`, Source/Curated IDs, pełnym replay ID/checksum,
coverage ID/checksum i checksumem runtime kwalifikacji. Ref `sales-view-sha256`
wiąże całą tę migawkę. Walidator odtwarza sumy i sprawdza zgodność wyniku
z migawką. Executor wymaga tego dowodu dla wyniku runtime i ponownie sprawdza
żądanie oraz środowisko. Historyczne fixtures zachowują dotychczasowy format.
[Schemat dowodu](../contracts/agent/v1/qualified-sales-evidence.v1.schema.json).

Obowiązuje maksymalnie 200 punktów dziennych, limit wszystkich zwracanych
serii, istniejące limity bajtów, 5 s dla narzędzia i deadline grafu. Planner
sprawdza pełne pokrycie oraz ustala limit serii w ramach istniejącego budżetu
przed admission. Dla porównania oba pełne okresy są sprawdzane osobno; różnica
nie powstaje, jeśli którykolwiek okres nie jest kwalifikowany.

`current` oznacza pełne potwierdzenie żądanego historycznego okresu w widoku
wiedzy `as_of`. Nie potwierdza zdrowia strumienia, najnowszego snapshotu lub
aktualnego stanu zapasu. Graf jawnie podaje przyczynę wstrzymania okresu.

Kandydaty konfiguracji `.native-sources.v1` wiążą oba adaptery, zmienione
schematy i pełny kod aplikacji. Wcześniejsze manifesty, receipts, etykiety
i artefakty modeli pozostają zachowane. Fixtures Source w testach są jawne,
a HTTP/graf korzystają z fake chat i testowego store. Ten odbiór nie oznacza
kwalifikacji produkcyjnego runtime, trwałego PostgreSQL/AI10 ani Sonnet/Titan.

```bash
make agent-security-test
make agent-evaluate PROVIDER=fake
make ci-checks
```
