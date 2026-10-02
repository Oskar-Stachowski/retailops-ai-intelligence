# AI 05 — rzeczywisty lokalny przepływ finalnego v12

Odbiór z 2026-10-02 obejmuje oryginalny, końcowy eksport AI 04 i osobno
zatwierdzone świeże wejście. [Zapis dowodów](05-v12-real-serving.json) wiąże
import, kwalifikacje, trzy trwałe zadania, decyzje registry i odczyt API.
**Lokalny przepływ AI 05 jest odebrany**, również na świeżym małym snapshocie.
Formalna publikacja wymaga zielonego Required CI i integracji
[AI PR #8](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/8)
oraz [source PR #81](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/81).
Wyniki CI należy sprawdzać dla bieżących commitów tych PR-ów.

## Świeży snapshot i trwała prognoza

Źródło ma 56 dni historii do **2026-10-01** i znane plany na 14 kolejnych
dni. Producent 0.9.1 przyjmuje jawne `forecast_plan_days`; wydłuża kalendarz,
assortment i routing, zachowując cutoff sprzedaży, zamówień, dziennych
obserwacji i ledgeru. Przyszłe ceny muszą być znane na historyczny cutoff.
Zadeklarowana kompletność obserwacji do 1 października jest niezależnie
przeliczana z danych przez producenta oraz importera AI.

Policy `forecast-v12-source-policy-1.1.0` przypina dokładny nowy source,
snapshot, feature set, curated descriptor i sygnaturę wejścia. Oryginalny
training pin, kod, recipe, lock i decyzja trzech MSE pozostają wymagane.
Zmiana wejścia wymaga nowej kwalifikacji i wszystkich 10 raportów przeglądu.
Polityka 1.0.0 i offline replay nadal wymagają oryginalnego źródła.

| Świeży odbiór | Rzeczywisty wynik |
|---|---|
| Import i przygotowanie | pełny native source, curated i features; 294 feature rows, 21 historii; wybrane 14 wierszy |
| Origin i watermark | 2026-10-01 23:59:59 UTC; `complete_through=2026-10-01`, deklaracja 2026-10-02 00:00 UTC |
| Kwalifikacja frozen v12 | dwie zgodne próby, 0 refit, 3 potwierdzone zamknięte dni jako `null` |
| Load / compute / RSS | 0,551 s / 0,0139 s / 83 165 184 B, około 79,3 MiB |
| Registry | osobne zatwierdzenie i wersja 4 w development; poprzednia wersja 1 zachowana |
| Pełny preflight archiwum | 206,87 s, przed przyjęciem zadania z limitem oczekiwania 600 s |
| Trwały batch z publikacją | 3,31 s, 14 wierszy; retry publikacji daje ten sam kompletny output |
| TCP read API | około 0,101 s dla pierwszej strony; 14/14 `current`, dokładne median/mean/interval i lineage |
| Restart | PostgreSQL, MLflow i obie instancje API; ten sam job, hash prognoz i view, nadal `current` |
| Poprzednie dane | oba historyczne joby / 28 wierszy, 224 segmenty oceny i 221 passed / 3 failed zachowane |

Świeży run `run-d0541e4d2c30cc4edc93c64f438fd7e2` ma SHA prognoz
`fd1781dfde3f9de32780f65a0e64319a18ab0fc6fc24721caa4d8df6f517652f`.
Odbiór API zakończył się 2026-10-02 o 16:06 UTC. Łącznie zapisano trzy udane
joby i **42 wiersze**. Czytnik ocenia aktualność przy każdym odczycie;
dobowa polityka oznaczy ten origin jako `stale` po 2026-10-02.

Przypięty obraz ma zachowany tag `retailops-ai05-v12-fresh-api:20261002`
i digest `sha256:04b9ad62e9990fa7acf512adfb21668da07fb53e4eb8403d99325ee69da2a6a8`.
Zaktualizowano tylko prywatne API odbiorowe. Host worker używa jawnego,
zweryfikowanego wheel i image pin operatora; ten dowód nie jest fizyczną
atestacją obrazu procesu hosta. Poprzedni read image również zachowano.

## Wcześniejszy odbiór historyczny

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

Powyższy wcześniejszy odbiór pochodzi z origin **2026-09-16**. Kalendarz źródłowy i obserwacje
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

Po wdrożeniu jawnej polityki świeżego wejścia przeszły 163 testy runtime,
inference, development, lifecycle, publikacji i kolejki, 44 testy izolacji
backupu oraz 60 przypadków w czystym Linuxie bez sieci. Dalsze 45 testów
sprawdzają watermark, dawne snapshoty i curated. Producent na aktualnym
`main` z tą poprawką przeszedł **772/772 testów danych**
w 183,61 s; Ruff i format objęły 122 pliki. AI Ruff sprawdził 534 pliki,
mypy 319 źródeł, pakiet zbudowano. Nie są to oddzielne, rozłączne zbiory,
więc nie sumujemy ich jako jednego pełnego przebiegu.

Poprzedni kompletny zdalny job `checks` miał 1707 passed i jeden błąd fixture
restore zależnego od lokalnego `compose.env`. Poprawkę e6419d0 sprawdził
czysty Linux; `persistence` tego przebiegu zaliczyło cały odbiór v12.
Kod AI 05 z commitu 556d349 przeszedł pełny Required CI: **1730/1730 testów**
w 2261,81 s oraz wszystkie cztery jobs, w tym rzeczywisty persistence.
[Zaliczone CI kodu](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37028425021)
nie zastępuje kontroli późniejszego commitu zapisującego ten raport.
Required CI finalnych publikowanych commitów obu repo pozostaje osobną bramką.

Pierwszy CI eksportera wykrył niezgodność bajtów schematów zwykłego snapshotu
ze starszym importerem przypiętym do 2574c06. Commit f1cf180 zachowuje jego
dokładną parę; rozszerzone schematy są eksportowane tylko przy jawnych planach.
Nie zmieniono przypiętego importera ani progu kontroli. Testy wymagają starych
sum SHA i odrzucają ponownie zapieczętowane mieszane pary. Nowy run source
Required CI sprawdza cały przepływ między repozytoriami.
Poniższe wcześniejsze wyniki zachowują historyczne ograniczenia.

Pełny lokalny przebieg pytest przed poprawką fixture RSS miał 1683 zaliczone
testy i jeden błąd: macOS rezerwował wyzerowane strony pamięci bez rzeczywistego
wzrostu RSS. Fixture zapisuje teraz strony; poprawiony przypadek przeszedł
dwie oddzielne próby. Dalsze 74 testy obejmują odczyt całej kampanii oraz
niezmienione limity forecastu i historycznych endpointów. Ruff sprawdził
530 plików, mypy 319 źródeł; kontrakty, dokumentacja i budowanie pakietu przeszły.
Nie oznacza to, że jeden pełny lokalny przebieg był cały zielony.

Pierwszy zdalny Required CI zatrzymał się w sprzątaniu osobnego testu
MLflow store: obraz nie miał oczekiwanych automatycznych etykiet właściciela.
Compose zapisuje teraz jawne `retailops.ai.build_project/build_service`.
Cleanup nadal odmawia usunięcia bez dokładnej zgodności projektu i usługi;
nie używa globalnego prune. Poprawka ma 57 testów oraz osobny odbiór dwóch
pustych obrazów `scratch` z rzeczywistym Compose i usunięciem tylko ich tagów.
To dowód oznaczenia własności, nie dodatkowy odbiór modelu czy backupu.

Kolejny Required CI przeszedł registry, restart, oba backupy oraz kolejkę,
ale wykrył zbyt wąskie założenie testu współbieżności. Drugi identyczny request
może otrzymać SQLSTATE `55P03` po 3 s oczekiwania na blokadę. Test ponawia go
dopiero po zakończeniu pierwszej transakcji i wymaga jednego zadania.
Konkurencyjne publikacje wymagają jednego sukcesu i dokładnie jednego nowego
manifestu z dwoma kompletnymi partycjami; inne błędy SQL nadal kończą test.
Natywna próba wymusiła blokady po 4,5 s przy niezmienionym limicie 3 s:
drugi request i druga publikacja otrzymały odmowę, a retry i restart nie
utworzyły duplikatów. Kontrole mają 62 testy regresji.

Pełny job `checks` zakończył się po 30 minutach na około 96% testów.
Przed zatrzymaniem ujawnił zależność unit testów backupu od lokalnego
`compose.env` oraz błąd subprocess testu inference. Fixture backupu tworzy
teraz własną prywatną konfigurację i nie wymaga prawdziwego Docker CLI;
34 przypadki przeszły w czystym Linuxie bez sieci. Limit całego joba wynosi
45 minut; nie zmieniono zestawu kontroli ani limitów workera.

Pomiar pamięci Linuxa też został poprawiony. `getrusage().ru_maxrss`
zachowuje użycie pamięci sprzed `exec`, co potwierdza
[dokumentacja Linux](https://www.man7.org/linux/man-pages/man2/getrusage.2.html).
Izolowana próba z rodzicem używającym 650 MiB wykazała odziedziczony wynik
około 663 MiB, choć bieżący program potrzebował około 20 MiB. Receipt
korzysta teraz z `VmHWM` bieżącego procesu. Rzeczywista dodatkowa alokacja
64 MiB podniosła pomiar do około 84 MiB. Brak lub niepoprawny pomiar blokuje
wykonanie; supervisor i limity pozostają wymagane. Przeszło 108 testów
pomiaru i runtime v1/v12. Pierwotne receipt i pomiary rzeczywistego v12
z macOS pozostają niezmienione.

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

Nowy odbiór wykonał osobne, prywatne narzędzia. Nie należy ponawiać całego
mutującego flow na działającym stosie. Generację producenta wykonano na
4fafaa0, a poprawkę przeniesiono do osobnego PR na `main` 6ccbe3d;
jego testowany head f1cf180 zawiera też zachowanie starych schematów zwykłego
eksportu. Gotowe świeże artefakty oraz ich zatwierdzenia pozostają niezmienne.
Dokładne receipts są w JSON.

```bash
.venv/bin/python /private/tmp/ai05-prepare-fresh-v12-source.py
.venv/bin/python /private/tmp/ai05-qualify-fresh-v12.py
.venv/bin/python /private/tmp/ai05-review-fresh-v12.py
.venv/bin/python /private/tmp/ai05-register-fresh-v12.py
.venv/bin/python /private/tmp/ai05-upgrade-fresh-api.py
.venv/bin/python /private/tmp/ai05-real-v12-decide.py promote fresh
.venv/bin/python /private/tmp/ai05-run-fresh-v12-flow.py
```

`complete.py` wewnętrznie wykonał także `rollback first` przez istniejący
`scripts/mlflow_v12_lifecycle.py decide`, z jawnym JSON request przez stdin,
decision ID, approval SHA, modelem, wersją i reason. Powtórzenie decyzji
nie tworzy nowej wersji ani release’u. Tokeny i hasła nie trafiały do argumentów
ani publicznych raportów.
