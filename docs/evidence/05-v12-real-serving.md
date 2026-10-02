# AI 05 — rzeczywisty lokalny przepływ finalnego v12

Odbiór z 2026-10-02 używa oryginalnego, końcowego eksportu AI 04 oraz
źródeł odtworzonych z zachowanego snapshotu. [Zapis dowodów](05-v12-real-serving.json)
wiąże import, kwalifikację, dwa trwałe zadania, decyzje registry i odczyt API.
To lokalny przepływ developerski. **AI 05 pozostaje otwarte**, ponieważ
trzeba jeszcze odebrać świeży mały snapshot i zdalny Required CI.

| Sprawdzenie | Rzeczywisty wynik |
|---|---|
| Pełny import kampanii | 664 pliki z manifestem, około 29,8 GiB, jeden run MLflow; ponowienie bez duplikacji |
| Integralność | Wszystkie pliki sprawdzone po stronie źródła, klonu APFS i HTTP MLflow; osobne inody |
| Kwalifikacja oryginalnego predictora | Dwie identyczne próby po 14 dni, 2 potwierdzone zamknięte dni jako `null` |
| Ładowanie / obliczenie próbne / pamięć | 0,530 s / 0,0125 s / około 79 MiB RSS |
| Trwałe zadania | 2 udane, 28 opublikowanych wierszy; identyczny hash prognoz w obu wydaniach |
| Batch z kontrolami workera | 10,94 s i 2,04 s; odczyt pierwszej strony API 0,146 s i 0,257 s |
| Pełny preflight archiwum | 205 s i 204 s; wykonany przed zleceniem nowych zadań |
| Lifecycle | Dwie osobno przejrzane wersje tego samego frozen v12, odrzucenie trzeciego kandydata i rollback do dokładnie poprzedniego release’u |
| Trwałość | Zadanie zachowuje model i input po restarcie; późniejszy rollback go nie przepina; oba opublikowane wyniki pozostają po kolejnym restarcie |
| Oryginalna jakość | 221 `passed`, 3 `failed`, cała kampania `not_ready`; przyjęte odstępstwa nie zostały przepisane |

Prognoza pochodzi z origin **2026-09-16**. Kalendarz źródłowy i obserwacje
kończą się 2026-09-30. API zwraca `stale`; czas wykonania/importu nie odnawia
świeżości. Nie zmieniano danych ani dat w oryginalnym eksporcie, nie trenowano
ponownie modelu i nie otwierano portfolio final testu.

Wszystkie dopuszczenia i decyzje dotyczą tylko
`retailops-demand-forecast-v12-development`. Domyślna instancja API nie
pokazuje tych prognoz ani ocen. Każda operacja używa prywatnego account,
roli, capability i scope. Raport zbiorczy wymaga dostępu do całych 100
produktów i 2 lokalizacji. Ograniczony viewer nie otrzymuje jego liczników
ani szczegółów. Katalog i raporty mają własny limit zakresu; nie poszerza
to limitów obliczeń ani odczytu dziennych prognoz.

Pierwsze zadanie demonstracyjne zostało przyjęte przed długimi sprawdzeniami
i przekroczyło 600 s oczekiwania. Zachowano je jako `cancelled` z audytem
`queue_deadline`, bez receipt i publikacji. Nie przedłużono terminu ani
nie podmieniono jego przypiętej wersji. Udane próby najpierw weryfikują
całe archiwum i ładują model, a dopiero potem zlecają pracę.

## Weryfikacja i granice

Pełny lokalny przebieg pytest przed poprawką fixture RSS miał 1683 zaliczone
testy i jeden błąd: macOS rezerwował wyzerowane strony pamięci bez rzeczywistego
wzrostu RSS. Fixture zapisuje teraz strony; poprawiony przypadek przeszedł
dwie oddzielne próby. Dalsze 74 testy obejmują odczyt całej kampanii oraz
niezmienione limity forecastu i historycznych endpointów. Ruff sprawdził
530 plików, mypy 319 źródeł; kontrakty, dokumentacja i budowanie pakietu przeszły.
Nie oznacza to, że jeden pełny lokalny przebieg był cały zielony.

Nowy odbiór [backup/restore](../forecast-v12-backup.md) działa z rewizją
`0019_v12_development`, prawdziwymi PostgreSQL dla AI i MLflow oraz 43 małymi
plikami fixture. Trwał 418 s i porównał cały stan obu baz i artefaktów.
To dowód mechaniki odtwarzania, **nie niezależny backup rzeczywistej kampanii**.
Klon APFS na tym samym dysku też nie jest takim backupem.

Źródło, przygotowane wejścia, kwalifikacje, przeglądy, prywatne konta,
reguły dostępu i lokalny artifact store są poza Git. Końcowy raport zawiera
wyłącznie identyfikatory, sumy kontrolne, pomiary i granice odbioru.
Osobny stos odbioru ma trwałe wolumeny PostgreSQL, artifact store na hoście
i migrację `0019_v12_development`. Dotychczasowego długotrwałego stosu nie migrowano.
Odbiór nie jest zgodą na produkcyjne wdrożenie.

## Wykonane komendy

Operacyjny odbiór wykonywano w `/private/tmp/retailops-ai-05-serving`.
Poniższe pliki są prywatnymi lokalnymi narzędziami; ich SHA i rozmiary są
w wersjonowanym raporcie. Komendy przyjmują oryginalny eksport i istniejące
zasoby. Nie należy ponawiać całej mutującej demonstracji na działającym stosie.
Publiczne odpowiedniki CLI opisują [import](../mlflow-v12-evidence.md),
[przegląd](../forecast-v12-release.md), [decyzje](../mlflow-v12-lifecycle.md)
i [worker](../forecast-v12-worker.md).

```bash
.venv/bin/python /private/tmp/ai05-verify-real-v12-import.py
.venv/bin/python /private/tmp/ai05-qualify-real-v12.py
.venv/bin/python /private/tmp/ai05-review-real-v12.py first
.venv/bin/python /private/tmp/ai05-register-real-v12.py first
.venv/bin/python /private/tmp/ai05-review-real-v12.py second
.venv/bin/python /private/tmp/ai05-register-real-v12.py second
.venv/bin/python /private/tmp/ai05-real-v12-decide.py promote second
.venv/bin/python /private/tmp/ai05-review-real-v12.py third
.venv/bin/python /private/tmp/ai05-register-real-v12.py third
.venv/bin/python /private/tmp/ai05-real-v12-decide.py reject third
.venv/bin/python /private/tmp/ai05-real-v12-complete.py
.venv/bin/python /private/tmp/ai05-real-v12-metadata.py verify
```

`complete.py` wewnętrznie wykonał także `rollback first` przez istniejący
`scripts/mlflow_v12_lifecycle.py decide`, z jawnym JSON request przez stdin,
decision ID, approval SHA, modelem, wersją i reason. Powtórzenie decyzji
nie tworzy nowej wersji ani release’u. Tokeny i hasła nie trafiały do argumentów
ani publicznych raportów.
