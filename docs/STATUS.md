# Aktualny status

**2026-10-07: AI 09 jest in_progress / not_ready; integracja po zamknięciu AI 07–08 jest na main.**
[PR #29](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/29)
ma komplet 14 zielonych jobów dla `4a0a6b5` i został scalony jako `5216e31`.
[CI dokładnego merge/main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37592860960)
jest zielone, z kompletem 14 wymaganych jobów. Nowy [eksporter fizyczny](physical-forecast-export.md)
odtwarza cechy i etykiety wszystkich pięciu ról z jednego prywatnego replayu.
Jego kontrolny profil `ai-load` nie kwalifikuje pełnego `ai-training` ani kampanii.
[Przyrost 09.12](evidence/09-12-prospective-campaign-journal.md) zachowuje historię
11 dawnych prób bez odtwarzania utraconych budżetów i dodaje trwały dziennik
prospektywnej kampanii. 52 testy odłączonego wheela przeszły, w tym SIGKILL,
fsync, konkurujące klienty oraz blokada końcowych danych przed freeze. To odbiór
mechanizmu i kontrolowanych metadanych; rzeczywista kampania jeszcze nie ruszyła.
[Integracja 09.11](evidence/09-11-main-integration.md) ma 420 zaliczonych testów,
114 kontroli po zmianie SciPy i 3 rzeczywiste testy TensorFlow/MLflow/reload.
Required CI integracji i 09.12 jest zielony na dokładnym headzie PR #29;
odbiór dokładnego merge/main `5216e31` także ma komplet 14 zielonych jobów.
AI 07 i AI 08 są gotowe na `origin/main`. Gałąź AI 09 integruje ich kontrakty,
kontrole i pakowanie; nie otwiera ponownie odbiorów wcześniejszych etapów.
[Ostatni odbiór AI 09.10](evidence/09-10-forecast-source-versions.md) zachowuje
historyczny stan zasobów i CI. Jego commit `7f10f8e` ma już zielone
[Required CI214](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37274516620),
a kontrola miejsca 2026-10-07 wykazała około 82 GiB wolnego, ponad rezerwę 50 GiB.
Te fakty usuwają wcześniejsze przeszkody, lecz nie zastępują odbioru większego
profilu ani końcowej kampanii.

Do zamknięcia pozostają: pełny audytowany eksport rzeczywistych pięciu ról,
integracja fitów i kalibracji, ocena RF/HGB/baseline/TensorFlow na trzech seedach,
robustness/niepewność/koszty, wspólne raporty trzech zastosowań, decyzje lifecycle
oraz pełny odbiór dokładnego commita na `main`. Dawne prywatne katalogi AI 09
w `/private/tmp` nie istnieją. Kod i opublikowane dowody są w Git; ciągłość
budżetów oraz ekspozycji wymaga odtworzenia dzienników lub jawnego,
konserwatywnego rozliczenia brakującej historii przed nowymi eksperymentami.
09.12 przygotowuje takie rozliczenie z przypiętych receiptów i wymaga jawnego
powiązania go w nowym prospektywnym protokole; nie kwalifikuje świeżości danych.
Nowe CI zachowuje wszystkie kontrole AI 07–08 oraz osobny wymagany job TensorFlow.

Szacunek zamknięcia na 2026-10-07: **24–40 godzin aktywnej pracy**, około
**3–5 dni roboczych**; przy problemach pełnego profilu rezerwa **5–7 dni**.
To szacunek implementacji i odbiorów, a nie zmierzony czas pełnej kampanii.
Największa niewiadoma to zasoby i czas canonical `ai-training`; kontrolne
14560 kluczy nie pozwala potwierdzić kosztu całego portfolio. Zakres czasu:

| Pozostała praca | Szacunek |
| --- | --- |
| Audytowany runner, eksport finalnej roli i pomiary pełnych profili | 6–10 h |
| Uczciwe strojenie/kalibracja i zamrożenie modeli/polityk | 4–7 h |
| Końcowa kampania trzech seedów dla trzech zastosowań | 6–10 h |
| Robustness, segmenty, niepewność i koszty | 3–5 h |
| MLflow/lifecycle oraz trzy karty i raporty | 3–5 h |
| Publikacja i odbiór dokładnego main z pełnym CI | 2–3 h |

**AI 07 — odbiór zaliczony, zakres `synthetic_ai_07_portfolio_v4`.**
Oba rzeczywiste modele v4 przechodzą 56/56 oryginalnych bramek jakości.
Świeży OCI/PostgreSQL 16/MLflow sprawdził komplet native publicznych rodziców,
gotowość API po migracjach, lifecycle, recovery, promocję, odrzucenie, rollback,
atomową publikację 1232 wyników, scoped HTTP i SIGKILL/restart.
[Końcowy odbiór](evidence/07-completion.md),
[manifest artefaktów](evidence/07-ready/capsules.json) oraz
[Required CI implementacji obu repozytoriów](evidence/07-ready/required-ci.json)
wiążą zakres, konkretne commity i zaliczone kontrole.
Publikacja została zatwierdzona i wykonana w
[AI PR #24](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/24)
oraz [source PR #97](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/97).
Pełne **AI 07 `ready` na `main`** jest zamknięte: PR #24 i #97 są scalone,
a odbiór integracji i obu main jest zaliczony. Wcześniejsze warunkowe wpisy
zachowują stan historyczny i nie otwierają ponownie tego etapu. [Zakres połączenia z main](evidence/07-main-integration.md)
opisuje wspólne migracje i zachowanie AI 05/08.

**2026-10-05: cały etap AI 08 jest READY.**
[Końcowe zamknięcie](evidence/08-28-final-ready.md) i
[receipt obu repo](evidence/08-28-final-ready.json) potwierdzają zaliczone dane,
jakość, kartę, rzeczywisty MLflow/lifecycle/batch/API, chroniony merge PR #14
oraz zielone Required CI jego HEAD i merge commitu na main.
9296 punktów ma 90 kontroli passed, 3 zaakceptowane ostrzeżenia i 0 blokad;
2543 testy pełnego CI, 206 focused tests, 12 review gates, 40 wyników i 15 attention.
Wymagane prace do zamknięcia AI 08: **0**. Wdrożenia produkcyjnego nie wykonano.

## Punkt wznowienia

Korzystaj z końcowego raportu i kwalifikowanej karty. AI 07 ma osobny odbiór;
AI 09/10 zachowują własne zależności. Wcześniejsze not ready nie otwierają ponownie AI 08.

## Historia wcześniejszych zakresów

Poniższe wpisy zachowują dawne wyniki, błędy i stan przyrostów w chwili odbioru.
Bieżący status AI 08 określa wyłącznie wpis READY i końcowy receipt powyżej.

**AI 08 — worker/HTTP/priority i pełny backup są zaliczone; final receipts/karta są przygotowane.**
[Przyrost 08.23](evidence/08-23-stockout-final-receipts-and-card.md) dodaje collector
sześciu światów, ponowną kontrolę bramek, kartę, qualifier i jawny review/import/lifecycle.
HEAD 549df93 ma zielony Required CI z 2478 testami. Nowy worker ma rzeczywisty odbiór
37257129973. Końcowe 9296 membership czeka na owner permission; nie oceniono outcomes.
Nowy zakres nadal wymaga własnego pełnego CI oraz rzeczywistych receipts final
orchestration/qualification. Niezależna jakość, osobna promocja, review/merge/main
są otwarte. **AI 08 pozostaje not ready.** Niżej jest historia wcześniejszych zakresów.

**AI 08 — real MLflow/SQL/backup są zaliczone; worker i priorytety czekają na pełny odbiór.**
[Przyrost 08.22](evidence/08-22-stockout-worker-and-priority.md) dodaje izolowane
obliczenia, fencing, widok priority przed projekcją scope i osobny current stockout.
132 focused tests przechodzą. Rzeczywiste odbiory 37255271458 i 37255965904 zaliczyły
SQL/backup oraz stockout MLflow; większy checker worker/HTTP/priority wymaga nowego CI.
Kampania 9296 punktów jest zamrożona i czeka na zgodę właściciela. Niezależna jakość,
kwalifikacja/karta, osobna promocja i końcowy CI pozostają otwarte.
**AI 08 pozostaje not ready.** Niżej zachowano historyczne zakresy.

**AI 08 — kampania 9296 punktów jest zamrożona; końcowa ocena czeka na zgodę.**
[Odbiór przyrostu](evidence/08-21-stockout-final-campaign-and-registry.md) zapisuje
sześć przygotowanych źródeł, evaluator z kontrolą zgody oraz byte verifier
i importer stockout MLflow. 118 focused tests przechodzi; rzeczywisty odbiór
nowego MLflow/SQL/backup jest jeszcze wymagany. Startup i pełne checks poprzedniego
CI są zielone, ale SQL 0021 wymagał poprawki kolejności operatorów JSON.
Worker, priorytety operacyjne, niezależna jakość, osobna promocja i końcowy odbiór
są otwarte. **AI 08 pozostaje not ready.** Niżej jest historia wcześniejszych zakresów.

**AI 08 — komplet sześciu źródeł jest przygotowany; poprawka CI czeka na odbiór.**
Trzy matching i trzy późniejsze kohorty 42/137/2026 zakończyły przygotowanie.
Końcowe etykiety nie były oceniane. Nowe CI wykryło błąd typu w generatorze
schematów i nieudany start loopback API; [poprawka](evidence/08-20-stockout-api-startup.md)
oddziela publiczne kontrakty od treningu/wykonania i sprawdza pełny zakres Mypy.
SQL 0021 wymaga jeszcze rzeczywistego odbioru. Poprzedni SQL 0020, pełny backup
oraz 2383 testy mają zielone CI. Zatwierdzenie polityki i kampanii, niezależna
jakość, MLflow, worker i końcowy odbiór są otwarte. **AI 08 pozostaje not ready.**
Niżej zachowano historyczny zakres wcześniejszych przyrostów.

**AI 08 — trwała kolejka i API są przetestowane; SQL 0021 czeka na CI.**
[Kontrakt](reference/stockout-jobs.md) i [odbiór](evidence/08-19-stockout-jobs.md)
opisują przypięte wersje, lease, retry, atomową publikację i fizyczny scope.
199 testów przechodzi. Poprzedni SQL 0020 i pełny backup/restore są zaliczone.
Trzy pierwotne kohorty są ukończone; późniejsze źródła 42/137 są przygotowane,
2026 jest w toku. Końcowa jakość, progi/capacity, MLflow, worker i końcowy
odbiór są otwarte. **AI 08 pozostaje not ready.** Niżej są wcześniejsze zakresy.

**AI 08 — podstawa lifecycle jest przetestowana; rzeczywisty SQL/registry oczekuje.**
[Kontrakt](reference/stockout-lifecycle.md) i [odbiór](evidence/08-18-stockout-lifecycle-foundation.md)
wiążą osobne stockout approval/binding/release ze wspólnym protokołem AI 05.
23 nowe i 114 istniejących testów regresji przechodzi. Nowa migracja wymaga
własnego rzeczywistego odbioru CI; lokalnej bazy źródła nie zmieniono.
CI producenta późniejszych źródeł jest zielone; dwie kohorty 1.8 liczą się.
Finalna jakość, zatwierdzona polityka, MLflow, durable batch/read i ready są otwarte.
**Cały AI 08 pozostaje not ready.**

**AI 08 — rzeczywisty nowy checkpoint ma zgodność native/wheel i zielone CI.**
[Odbiór 08.17](evidence/08-17-stockout-remote-checkpoints.md) potwierdza
wybrany model i wszystkie segmenty na danych wyboru. Matching seedy 42/137
są przygotowane; 2026 jest jeszcze w toku. [Późniejsze źródła](reference/stockout-future-sources.md)
mają osobne prospektywne profile/workflow, bez fitów lub otwarcia TEST.
Zgoda na politykę, rzeczywiste późniejsze źródła, niezależna jakość, lifecycle,
batch/read API i finalny odbiór pozostają otwarte. **Cały AI 08 pozostaje not ready.**

**AI 08 — późniejsza kalibracja i przenośny runtime mają osobny protokół development.**
[Kontrakt](reference/stockout-selection-runtime.md) i
[odbiór przyrostu](evidence/08-16-stockout-selection-runtime.md) dodają
pełny stan kalibratora, propozycję progów/capacity, publiczny replay wejść
i odrębne uprawnienia stockout. TRAIN fituje model, TUNE kalibrator,
CALIBRATION wybiera wariant; wyniki wyboru nie są niezależną oceną jakości.
Stare pakiety oraz ich niezaliczone bramki pozostają zachowane.
Trzy matching kohorty 30 × 2 × 102 na seedach 42/137/2026 mają przygotowany
osobny workflow GitHuba. Nowy rzeczywisty native/wheel i CI wymagają receipt.
Rezerwa lokalnego dysku pozostaje 50 GiB. Final test, zatwierdzone progi,
lifecycle/MLflow, trwały batch/read API i końcowy odbiór są jeszcze otwarte.
**Cały AI 08 pozostaje not ready.** Poniżej zachowano historyczny zakres.

**AI 08 — niezależna ocena i karta partycji działają; jakość pozostaje `not_ready`.**
[Receptura](reference/stockout-independent-qualification.md) i
[odbiór](evidence/08-15-stockout-independent-development.md) mają native/wheel
z identycznymi bajtami oraz pełnym replay rzeczywistych rodziców.
Nowy pilot 19 × 2 × 102 daje **3876 origin / 1473 development**, wobec
2856 / 1043. Cały pipeline trwa 1243,65 s, peak RSS 492,09 MiB,
allocated scratch 325,79 MiB i zachowuje rezerwę 50 GiB.
Model fituje 370 wcześniejszych punktów, sigmoid 232 późniejsze;
321 tune wybiera rodzinę, 313 kolejnych punktów ocenia ją niezależnie.
Wszystkie 8 kategorii mają obie klasy na tune. Wybrany LR without_upstream
ma niezależny AP 0,991449, ale sigmoid pogarsza Brier 0,035440→0,050294.
Dwie kategorie przekraczają proponowany ECE 0,15; constrained segment
ma tylko 3 negatywne wobec wymaganego minimum 5. Wyniki nie są uznane
za zaliczenie jakości. 100 focused/dependency testów przeszło bez ostrzeżeń.
Większa próba, robustness, zatwierdzone progi/capacity, finalna kampania,
lifecycle i batch/read API pozostają otwarte. Test końcowy nie jest oceniony,
model nie jest promowany; **cały AI 08 pozostaje not ready**.
Poniżej zachowano historyczny zakres wcześniejszych przyrostów.

**AI 08 — kompletny pipeline przeszedł odbiór większego pilota.**
[Pomiar](reference/stockout-resource-probe.md) i
[odbiór](evidence/08-14-stockout-resource-probe.md) obejmują producenta,
eksport/import, curated, rzeczywiste partycje, development i sześć modeli.
Jawny `ai-load` 14 × 2 × 102 ma **2856 punktów**, wobec 1632 smoke.
Train/tune/calibration: **598/227/218**, razem 1043 wobec 605.
Całość trwa 719,81 s, peak własnego drzewa RSS 622,23 MiB, allocated scratch
259,23 MiB; 50 GiB rezerwy jest zachowane. Przyjęte wejścia i modele są
zachowane oddzielnie do niezależnej oceny. Wariant 4480 odrzucono przed
startem przez estymację RAM. Pełne `ai-dev`/`ai-training` są nieodebrane.
Regeneracja smoke zachowuje sześć modeli/wyników, używając nowych prawdziwych
label/temporal IDs wynikających z nowych timestampów manifestu. Stare
573 wejścia/v1 rodzice i 302 pliki partycji są zachowane.
29 nowych kontroli jest zaliczonych; Ruff/mypy/docs oraz pełny rzeczywisty
smoke i pilot mają odbiór. Rodzic 4faaf4b ma zielone Required CI;
nowy commit wymaga swojego CI. Tune wybiera provisional LR without_upstream,
a kategorii not_evaluable jest 1/8 zamiast 5/8. To nie jest niezależny odbiór
jakości/kalibracji. Nowa karta, ta ocena, progi/capacity i lifecycle/batch/read
pozostają otwarte. Final test i promocja nie są wykonane;
**cały AI 08 pozostaje not ready**. AI 05/v12 i starsze pakiety są zachowane.

**AI 08 — upstream 2.1 jest podłączony do treningu bez nadmiarowego replay.**
[Kontrakt](reference/stockout-temporal-series.md) i
[odbiór](evidence/08-13-stockout-temporal-series.md) dodają osobny temporal 2.1.
1632 comparison/membership oraz sześć modeli zachowują wyniki; rzeczywiste
train/tune/calibration mają 340/135/130 punktów i nowe parent IDs.
Cechy/upstream/etykiety są odtwarzane raz w prywatnym kontekście; cała
weryfikacja kończy się przed zwróceniem development. Test to 131 membership,
bez wektora celów/metryk. Native i odłączony wheel/CLI zachowują bajty/ID;
79 modułów pochodzi z instalacji, producent jest nieimportowalny.
573 oryginalne wejścia/v1 rodzice i 287 plików starych partycji są zachowane.
Trzy pary assemblera mają medianę 329.81→177.06 s
(-46.3%), RSS 226.00→226.33 MiB;
RSS/scratch są podobne. To pomiar smoke/assemblera przy równoległym CI,
bez odbioru większego profilu lub całego pipeline.
Pełny `make ci-local` przeszedł: **2133/2133 testów**, 0 ostrzeżeń, 2849.97 s testów; 21 targetów, pakiet, Compose config i oba skany sekretów.
48 nowych przypadków obejmuje także kontrolę limitu 4096 output files przed
publikacją. Rodzic 92b8d5f ma zielone Required CI PR i push;
nowy commit wymaga własnego CI. Większy profil/preflight, niezależna ocena,
karta z nowych rodziców, progi i lifecycle/batch/read pozostają otwarte.
Stare pakiety/ID, AI 05 i v12 są zachowane; cały AI 08 pozostaje not ready.
Poniższe przyrosty zachowują historyczny zakres odbioru.

**AI 08 — upstream ma selekcję jednej fizycznej serii i globalną walidację.**
[Kontrakt 2.1](reference/stockout-upstream-series.md) i
[odbiór](evidence/08-12-stockout-upstream-series.md) zachowują wszystkie
1632 prognozy, comparison i sześć modeli obecnej próbki. Selekcja ma
najwyżej 198 zamiast 3625 rekordów
oraz 247,260 zamiast 4,166,485 B.
Ograniczony decoded cache ma 1024 rekordy / 1 MiB, schema wczytywane raz.
Globalne znane wersje i efektywne trasy są sprawdzane przed fizycznym filtrem.
Native i odłączony wheel zachowują identyczne bajty, 573 wejścia/rodzice
pozostały bez zmian. Regresja ukierunkowana ma 118/118 przypadków,
w tym 48 nowych. Pełny `make ci-local` przeszedł: 2085/2085 testów,
0 ostrzeżeń, wszystkie targety, pakiet, Compose config i skany sekretów.
Trzy pary buildów mają medianę 127.23 → 149.12 s,
peak RSS 127.17 → 125.83 MiB;
nie wykazano przyspieszenia lub istotnego zmniejszenia całego RSS na smoke.
To usunięcie globalnego panelu Python, bez odbioru większego profilu.
Rodzic `df61862` ma zielone Required CI PR i push; nowy commit wymaga własnego CI.
Temporalny trening 2.0 pozostaje odebrany z upstream 2.0. Podłączenie
upstream 2.1 i spójny przebieg bez nadmiarowego replay, pozostałe limity,
większy profil/RSS/scratch, niezależna ocena, progi, karta i lifecycle/batch/read
API pozostają otwarte. Stare pakiety/ID, AI 05 i v12 pozostają zachowane;
final test nie jest oceniony, cały AI 08 pozostaje not ready.
Poniższe przyrosty zachowują historyczny zakres odbioru.

**AI 08 — temporalne połączenie partycji jest podłączone do treningu.**
[Kontrakt](reference/stockout-temporal-storage.md) i
[odbiór](evidence/08-11-stockout-temporal-storage.md) dodają osobny pakiet
comparison/membership i assembler development. Wszystkie pola 1632
comparison i 1632 membership są identyczne z v1. Rzeczywiste wejścia
train/tune/calibration mają 340/135/130 punktów; końcowy test pozostaje
131 membership bez wektora celów i metryk. Sześć modeli zachowuje
pipelines, model IDs, wyniki i report; nowy development ID wiąże bundle
rodziców. Zapis ma 178 960 B zamiast 1 890 509 B,
o 90,5% mniej. Zainstalowany wheel odtworzył identyczne pliki,
manifest, development i modele; 573 wejścia/rodzice pozostały bez zmian.
Regresja ukierunkowana ma 153/153 zaliczeń, w tym 27 nowych. Ostrzeżenie
joblib dotyczy blokowanego odczytu fizycznych rdzeni CPU w sandboxie.
Walidacja lokalna ma 2037/2037 unikalnych przypadków: 1903 poza modułami
wymagającymi uprawnień i całe 134 przypadki tych niezmienionych modułów
powtórzone z dostępem do localhost/statystyk procesów. Pierwszy
`make ci-local` miał exit 2 przez bind i sysctl blokowane w sandboxie;
receipt jawnie zachowuje 28 failures / 2 errors i nie opisuje go jako exit 0.
Wszystkie targety, pakiet, Compose config i skany sekretów są zaliczone.
Końcowe dowody mają ponowne kontrole dokumentacji i sekretów. Poprzedni `077add2` ma zielone
Required CI PR i push; nowy commit wymaga własnego CI w draft PR #14.
Limity pozostają: 10 000 kluczy, kanoniczny development 16 MiB,
qualification JSON 4 MiB i globalny upstream 20 000 wierszy / 16 MiB.
Cały RSS/scratch i większy profil nie są odebrane. Pozostają selekcja
upstream po seriach, preflight i jawny większy profil, niezależna ocena,
progi, karta z nowych rodziców i lifecycle/batch/read API. Stare pakiety/ID,
AI 05 i v12 pozostają zachowane; cały AI 08 pozostaje not ready.
Poniższe przyrosty zachowują historyczny zakres odbioru.

**AI 08 — ograniczony upstream 2.0 zachowuje pełną próbkę.**
[Kontrakt](reference/stockout-upstream-storage.md) i
[odbiór](evidence/08-10-stockout-upstream-storage.md) dodają osobny pakiet,
pełny replay cech 2.2, jednorazowy panel faktów i chronologiczny Parquet.
Wszystkie pola 1632 prognoz, comparison i sześć modeli są identyczne z v1.
Bundle ma 1 103 923 B zamiast JSON 2 127 982 B, o 48,1% mniej.
Odłączony wheel odtworzył identyczny manifest, pliki i pełny iterator;
48 modułów konsumenta pochodziło z zainstalowanego pakietu.
Wszystkie 573 pliki wejść/rodziców pozostały bez zmian.
Testy ukierunkowane mają 80/80 zaliczeń, w tym 27 nowych, bez ostrzeżeń.
Trzy pary pełnego przygotowania, razem z replay rodzica cech, dały medianę
76,13 → 55,74 s i RSS 204,83 → 137,92 MiB. Pomiar łączy także korzyść
cech 2.2 i trwał przy równoległym CI; nie kwalifikuje większego profilu.
Lokalna walidacja ma 2010/2010 przypadków w dwóch częściach, bez ostrzeżeń,
wszystkie targety Makefile, pakiet, Compose config i czyste skany sekretów.
Pierwszy pełny przebieg ujawnił rzeczywisty timeout testu scope kolejki v12;
kontrolowany zegar i dwa osobne testy expiry/heartbeat dotyczą tylko testów.
Limit produkcyjny 120 s i predictor pozostają zachowane. Cały zmieniony
moduł oraz pozostały runtime ponownie przeszły; receipt jawnie zapisuje
nieudaną pierwszą próbę. Końcowe dowody mają ponowne kontrole dokumentacji.
Poprzedni `d083e0a` ma zielone Required CI PR i push.
Panel nadal ma globalny limit 20 000 wierszy / 16 MiB; cały RSS/scratch
większego pipeline nie jest odebrany. Comparison, split i trening pozostają
v1, a reader upstream nie wybiera ról development. Pozostają ich połączenie,
globalna walidacja z selekcją upstream po seriach, większy profil,
niezależna ocena, progi i lifecycle/batch/read API.
Stare pakiety/ID, AI 05 i v12 pozostają zachowane. Final test nie jest
oceniony; cały AI 08 pozostaje not ready. Poniższe przyrosty zachowują zakres.

**AI 08 — prywatne partycje etykiet 2.0 zachowują pełną próbkę.**
[Kontrakt](reference/stockout-label-partitions.md) i
[odbiór](evidence/08-09-stockout-label-partitions.md) dodają jednorazową bazę,
ledger jednej fizycznej serii oraz batch Parquet. Wszystkie pola 1632
etykiet i sześć wariantów modeli są identyczne z v1; nowe części mają
110 484 B zamiast JSON 666 623 B. Pełny iterator sprawdza całość przed
pierwszym punktem i ponawia kontrole przed konsumpcją części.
Testy ukierunkowane mają 98/98 zaliczeń, w tym 31 nowych, bez ostrzeżeń.
Końcowe trzy pary buildów po normalizacji UTC mają medianę RSS
112.73 → 102.48 MiB (-9.1%), wall
12.84 → 12.66 s. To mała próbka, bez odbioru całego RSS/scratch.
Odłączony wheel odtwarza identyczne pliki i iterator; 573 wejścia/rodzice
pozostają bez zmian. Pełny `make ci-local` przeszedł: 1981/1981 testów bez ostrzeżeń,
wszystkie targety, pakiet, Compose config i oba skany sekretów. Końcowe
dowody mają ponowne kontrole dokumentacji i sekretów; nowy commit wymaga
własnego Required CI w draft PR #14.
Stare pakiety/ID, AI 05 i v12 pozostają zachowane. Upstream, comparison,
split i trening nadal korzystają z v1. Pozostają ich czytniki i temporalne
połączenie, większy profil i odbiór całego pipeline, niezależna ocena,
progi i lifecycle/batch/read API. Cały AI 08 pozostaje not ready.
Poniższe przyrosty zachowują swój zakres.

**AI 08 — indeks historii 2.2 zachowuje pełną próbkę 102 dni.**
[Kontrakt](reference/stockout-history-index.md) i
[odbiór](evidence/08-08-stockout-history-index.md) obejmują sumy prefiksowe
ledgeru i dzienne grupowanie znanego popytu, bez zmiany pakietów v1/v2.0/v2.1.
Wszystkie 1632 punkty, Parquet i sześć wariantów modeli są identyczne;
native i wheel odtworzyły te same pliki, a 573 wejścia/rodzice zachowały hash.
Trzy pary świeżych buildów dały medianę 21,32 → 18,42 s, CPU 20,57 → 17,71 s;
mediana próbkowanego peak RSS wyniosła 131,75 → 131,98 MiB.
Sorty ledgeru spadły z 45 696 do 1632. Indeks powstaje na granicy wiedzy
każdego origin; późna korekta nie nadpisuje dawnych danych.
Przyrost ma 150/152 testy ukierunkowanych, w tym 37 nowych,
bez ostrzeżeń. Pełny `make ci-local` przeszedł:
1950/1952 testy bez ostrzeżeń, wszystkie targety, pakiet, Compose
config i oba skany sekretów. Końcowe receipt mają ponowne kontrole
dokumentacji i sekretów; nowy commit wymaga własnego CI w draft PR #14.
Poprzedni commit `858abcf` ma zielone Required CI PR i push.
Etykiety, upstream, split i trening nadal przyjmują v1. Pozostają ich
partycjonowanie, rzeczywisty większy profil i odbiór całego pipeline,
niezależna ocena, progi i lifecycle/batch/read API. Final test nie jest
oceniony, cały AI 08 pozostaje not ready. Poniższe przyrosty zachowują zakres.

**AI 08 — ograniczony odczyt faktów z dysku zachowuje wyniki małej próbki.**
[Kontrakt 2.1](reference/stockout-disk-facts.md) i
[odbiór](evidence/08-07-stockout-disk-facts.md) obejmują prywatny magazyn
SQLite oraz cache jednej fizycznej serii, bez zmiany kodu v1/v2.0.
Wszystkie 1632 punkty, pliki Parquet i sześć wariantów modeli development
są identyczne z wcześniejszą ścieżką. Native i zainstalowany wheel
odtworzyły te same pliki; 573 wejścia i rodzice pozostały bez zmian.
W osobnych świeżych procesach build miał 131,5 zamiast 175,0 MiB peak RSS
i trwał 15,44 zamiast 18,75 s. Pomiary obejmują replay curated i cechy,
bez kwalifikacji budżetu większego pipeline. Przyrost ma 113/113 testów
ukierunkowanych, w tym 30 nowych. Pełny `make ci-local` przeszedł:
1913/1913 testów bez ostrzeżeń, wszystkie targety, pakiet, Compose
config i oba skany sekretów. Końcowe receipt mają ponowne kontrole
dokumentacji i sekretów; nowy commit wymaga własnego CI w draft PR #14.
Poprzedni commit `ebe1f45` ma zielone Required CI PR i push.
Pozostają kumulacyjny ledger i ruchome okna, partycjonowanie pozostałych
rodziców/treningu, rzeczywisty większy profil oraz niezależna ocena,
progi i lifecycle/batch/read API. Final test nie jest oceniony;
cały AI 08 pozostaje not ready. Poniższe przyrosty zachowują swój zakres.

**AI 08 — partycje cech v2 zachowują całą próbkę 102 dni.**
[Kontrakt](reference/stockout-partitions.md) i
[odbiór](evidence/08-06-stockout-partitions.md) obejmują 1632 identyczne
punkty i identyczne modele development po rzeczywistym odczycie Parquet.
Zapis ma 1 546 444 B zamiast 10 249 881 B; indeks ogranicza skany obcych
serii i zachowuje granicę wiedzy oraz wszystkie stare wersje lineage.
Natywny build/rebuild/verify i zainstalowany wheel odtworzyły identyczne
ID/bajty. Wszystkie 567 odtworzonych wejściowych plików pozostały
niezmienione. Regresja przyrostu ma 83/83 testów, w tym 30 nowych.
Pełny `make ci-local` przeszedł: 1883/1883 testów bez ostrzeżeń,
wszystkie targety, pakiet, Compose config i oba skany sekretów.
Końcowe receipt mają ponowną kontrolę dokumentacji i sekretów;
nowy commit wymaga własnego CI w draft PR #14.
Wcześniejsza karta `ab22763` ma zielone Required CI PR i push.
Pozostają kumulacyjne indeksy i okna historii, ograniczony odczyt wejścia,
pozostałe rodzice i trening z partycji oraz odbiór większego profilu.
Final test nie jest oceniony, progi niezatwierdzone i model niepromowany;
cały AI 08 nie jest ready. Poniższe przyrosty zachowują historyczny zakres.

**AI 08 — karta i wyjaśnienia development mają odbiór natywny oraz wheel.**
[Kontrakt karty](reference/stockout-model-card.md) i
[pomiary](evidence/08-05-stockout-card.md) obejmują trzy tabele LR,
20 grup permutation importance wybranego HGB i 135 zestawów lokalnych
faktów PIT. Karta odtworzyła identyczne ID/bajty z odłączonego pakietu;
ponownie zahashowane zmiany karty i modelu zostały odrzucone. Wszystkie
278 wejściowych plików oraz identity przygotowania/treningu pozostały
niezmienione. Regresja przyrostu ma 49/49 testów, w tym 18 nowych.
Pełna regresja ma 1853/1853 testów. Wszystkie targety `check` i oba skany
sekretów są zaliczone; fałszywy alarm na polskim zdaniu usunięto korektą
dokumentacji i ponownym `make docs-check secrets`, bez zmiany skanera.
Nowy commit wymaga własnego CI w draft PR #14.
[Projekt większego profilu](reference/stockout-profile-resources.md) określa
partycje, indeksy PIT i pomiary; większa generacja nie została uruchomiona.
Pełny profil, niezależna ocena kalibracji, threshold policy i
lifecycle/batch/read API pozostają otwarte. Final test nie jest oceniony,
model niepromowany; cały AI 08 nie jest ready. Poniższe przyrosty AI 08
zachowują historyczny zakres pomiarów.

**AI 08 — LR/HGB mają pierwsze wyniki development na próbce 102 dni.**
[Kontrakt](reference/stockout-training.md) i
[odbiór](evidence/08-04-stockout-models.md) obejmują sześć porównywalnych
wariantów, 340 punktów train, 135 tune oraz osobny sigmoid na 130 punktach
calibration. Wybór provisional opiera się tylko na tune. Kalibracja po
dopasowaniu jest diagnostyką in-sample; final test nie jest oceniany.
Natywny build/rebuild/replay oraz zainstalowany wheel odtworzyły identyczne
modele; test ponownie zahashowanej zmiany wag został odrzucony. Regresja
ma 47/47 testów. Pełny `make ci-local` przeszedł: 1835/1835 testów,
wszystkie targety, pakiet i skany sekretów. Commit `4a7a76f` ma zielone
Required CI PR i push w draft PR #14. Pięć z ośmiu kategorii tune
ma ranking `not_evaluable` z powodu jednej klasy, bez wyjątku jakości.
Późniejsza karta ma osobny odbiór opisany powyżej. Pełny profil i niezależna
ocena, threshold policy oraz lifecycle/batch/read API pozostają otwarte;
cały AI 08 nie jest ready.

**AI 08 — historyczna prognoza bazowa ma odbiór natywnej próbki 102 dni.**
[Kontrakt](reference/stockout-upstream.md) i
[dowody](evidence/08-03-stockout-upstream.md) obejmują rekonstrukcję prognozy
z wiedzy każdego dnia oraz identyczne wejścia wariantów z prognozą i bez niej.
Prognoza jest dostępna dla 1015/1104 bazowych punktów `eligible` i wszystkich
736 punktów dopuszczonych przez split (340/135/130/131).
Regresja ma 102/102 testów; zainstalowany wheel odtworzył identyczne wyniki
z publicznych danych, bez etykiet i truth. Pełny `make ci-local` przeszedł:
1816/1816 testów, wszystkie targety, pakiet i skany sekretów. Commit
`aa6b953` ma zielone Required CI PR i push; draft PR #14 pozostaje otwarty.
Późniejsze wyniki porównania modeli i diagnostyki kalibracji mają odbiór 08.4;
final test pozostaje nieoceniony i cały AI 08 nie jest ready.

**AI 08 — wcześniejsze cechy PIT i podział w czasie mają pełny odbiór lokalny.**
[Kontrakt cech](reference/stockout-features.md) i
[dowody 102 dni](evidence/08-02-stockout-features.md) obejmują 1632 origin:
1104 kwalifikujące się cechy, 432 istniejące braki i 96 braków danych.
Podział dopuszcza 340/135/130/131 punktów train/tune/calibration/test;
279 okien wykluczono przy granicach okresów. Regresja ma 144/144 testów;
zainstalowany wheel odtworzył identyczne wyniki. Pełny `make ci-local` przeszedł,
w tym 1800/1800 testów, pakiet i skany sekretów.
Pierwszy zakres [etykiet](evidence/08-01-stockout-labels.md) ma zielone
Required CI PR i push na `172496b`. Nowszy przyrost w roboczym
[PR #14](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/14)
wymaga własnego CI; main nie zawiera jeszcze tego zakresu.
Trening LR/HGB i dopasowanie sigmoid opisuje późniejszy odbiór 08.4.
Niezależna ocena jakości i registry/batch/read API pozostają otwarte.
Historyczne raporty przyrostów zachowują swój zakres.

**AI 05 — ready po scaleniu PR-ów i zielonym Required CI obu mainów.**
[Końcowy raport publikacji](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/evidence/ai/05/final/README.md)
wiąże PR #8 AI, PR #81 źródła i końcową dokumentację PR #82.
Poniższe wcześniejsze sekcje AI 05 zachowują historyczny zakres prób;
ich informacje o otwartych blokadach zostały zastąpione końcowym odbiorem.

**AI 05 — lokalny przepływ finalnego v12 jest odebrany także na świeżym snapshocie.**
[Raport i pomiary](evidence/05-v12-real-serving.md) oraz
[wersjonowany zapis dowodów](evidence/05-v12-real-serving.json) obejmują cały
import 664 plików / około 29,8 GiB, kwalifikację oryginalnego predictora,
trzy działające wersje registry, trzy udane trwałe batch’e i 42 opublikowane wiersze.
Restart zachowuje zadanie i input; rollback nie przepina wcześniej przyjętego
runa; odrzucenie kandydata nie zmienia runtime. Wyniki obu historycznych wydań są identyczne.

Świeży source ma 56 dni historii do 2026-10-01 oraz 14 dni jawnych znanych
planów, bez przyszłych obserwacji. Pełny import i niezależna kontrola watermarku,
osobna source policy 1.1.0, wszystkie 10 raportów, wersja development 4 i worker
dały **14/14 `current`**, w tym 3 potwierdzone zamknięte dni jako `null`.
Batch z publikacją trwał 3,31 s, pierwsza strona API około 0,10 s.
Restart PostgreSQL/MLflow/API zachował job, dokładny hash i aktualny odczyt.
Kod producenta na aktualnym `main` przeszedł 772/772 testów danych.
Kod AI 05 z 556d349 ma zielony Required CI oraz 1730/1730 testów;
późniejszy commit raportu wymaga własnej kontroli publikacji.

To osobny [lokalny namespace developerski](forecast-v12-development.md).
Domyślne API nie pokazuje jego danych. Pełna ocena 100 produktów / 2 lokalizacji
zachowuje 221 passed, 3 failed i oryginalne `not_ready`; ograniczony viewer
nie widzi zbiorczego raportu. Origin 2026-09-16 pozostaje historyczny i API
zwraca `stale` dla wcześniejszych prognoz. Nie było refitu ani zmiany oryginalnych artefaktów AI 04.

Nowy odbiór [backup/restore](forecast-v12-backup.md) sprawdza cały stan obu baz
PostgreSQL i artefaktów na małych fixture z head `0019_v12_development`.
Nie jest niezależnym backupem rzeczywistej kampanii; klon APFS na tym samym
dysku także nim nie jest. Osobny trwały stos odbioru ma tę migrację;
dotychczasowego długotrwałego stosu nie zmieniono.

**Formalna publikacja AI 05 pozostaje otwarta:** wymagane są zielone Required CI
bieżących commitów i integracja [AI PR #8](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/8)
oraz [source PR #81](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/81).
Lokalny świeży snapshot, kwalifikacja i odbiór batch/API są zakończone.
Produkcji nie dopuszczono. Historyczne raporty poszczególnych przyrostów
zachowują swoje wcześniejsze wyniki i granice.

Aktualizacja: **2026-10-02**. **Etap 11 — RAG jest odebrany lokalnie.**
[Instrukcja użytkowa](knowledge-semantic.md) opisuje rzeczywiste embeddings,
przygotowanie, kwalifikację, aktywację i rollback. [Końcowy odbiór](evidence/11-completion.md)
wiąże implementację z pomiarami i ograniczeniami. Etap 11 jest opublikowany na
chronionym `main` przez [PR #4](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/4);
[Required CI na main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36461392661)
ma `success`.

**AI 04 — `ready`, finalna wersja v12, z jawną akceptacją trzech odstępstw.**
Właściciel projektu zakończył iterację developerską na v12 2026-10-01.
[Decyzja odbioru](evidence/04-v12-acceptance.md) obowiązuje po przyjęciu tego
commitu przez PR i zielonym Required CI chronionego `main`.
[Wersjonowany zapis decyzji](evidence/04-v12-acceptance.json) wiąże akceptację
z jednym konkretnym eksportem, pełnymi metrykami i dowodami odtworzenia.

Pełna kampania v12 obejmuje **64/64 kohorty i 27 396 096 wierszy prognoz**.
Oryginalny protokół jakości nadal daje **221 passed / 3 failed** oraz
`forecast_model_status=not_ready` i `quality_qualification_status=not_ready`.
Zaakceptowane odstępstwa MSE wynoszą **+0,204738%, +0,000619%, +0,043292%**.
Nie przepisano ich na zaliczone i nie zmieniono progów oceny.
`stage_status=ready` oznacza świadomy odbiór etapu przez właściciela,
nie nowy wynik statystyczny ani zgodę na wdrożenie produkcyjne.

[Finalne v12](forecast-functional-v12.md) ma zaliczony niezależny replay,
trwały eksport **663 plików / 31 994 594 655 B** i weryfikację rzeczywistego
runu z odłączonego wheel. Kontrola `make forecast-acceptance-check` sprawdza
oryginalne sumy SHA-256, komplet metryk i dokładny zakres trzech wyjątków.
Nie dopuszcza innego runu, v13 ani rozszerzenia decyzji na promocję modelu.

V13 jest **superseded**: [przygotowanie 36900199207](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36900199207)
zostało anulowane po wyborze v12. Lokalne oczekiwanie na ocenę i kolektor
zatrzymano przed oceną holdoutów v13. Zachowano istniejące pliki, rezerwacje
seedów, freeze i historię; automatyczne uruchamianie generacji po pushu wyłączono.

Odbiór kodu: [PR #7](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/7),
bez omijania ochrony `main` i Required CI. Źródło zostało przyjęte przez
[PR #77](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/77).
Dalsze prace AI 05 korzystają z eksportu v12 i osobnego adaptera dwóch celów;
MLflow, promocja, batch i serving mają własny odbiór. Portfolio final test
pozostaje nietknięty. Historyczne wyniki [04.7](evidence/04-07-quality.md),
[04.8](evidence/04-08-handoff.md), [korekt](evidence/04-quality-remediation.md)
i [v11](forecast-functional-v2.md) zachowują pierwotne statusy.

**AI 05.1 — lokalny tracking i magazyn MLflow:** istniejący z AI 01
PostgreSQL, rola i trwały wolumen mają teraz [backup/restore i politykę
retencji](mlflow-store.md). [Odbiór](evidence/05-01-store.md) sprawdza
rzeczywiste przeniesienie eksperymentu, runu i artefaktu do pustego projektu
oraz odczyt po odtworzeniu. Kolejny zakres to import evidence 04.8 do
MLflow. Model pozostaje `not_ready`; registry, promocja, batch i API nie
są jeszcze częścią odbioru 05.1.

**AI 05.2 — historyczne evidence 04.8 w MLflow:** [importer i semantyka
runu](mlflow-evidence.md) zachowują oryginalne ID, czasy eksportu, lineage,
metryki wraz z ważnością i pełne archiwum z sumami kontrolnymi. [Odbiór
lokalny](evidence/05-02-import.md) potwierdza rzeczywisty import i powtórzenie
bez drugiego runu. Quality nadal ma 145 passed, 79 failed, 8 not_ready;
status modelu to `not_ready`. Registry, promocja, batch i API pozostają do
wykonania w kolejnych zakresach AI 05.

**AI 05.3a — kontrola registry przed wersją:** [review i odrzucenie](mlflow-registry.md)
sprawdziły import 04.8, zapisały audyt z rolą `promoter` oraz powtórzyły
decyzję bez duplikatu. [Odbiór](evidence/05-03-review.md) wskazuje runy i
backup. `retailops-demand-forecast` nie ma wersji ani aliasów; pełna
promocja/rollback na modelu AI 04 czekają na jego kwalifikację.

**AI 05.3b — Registry i recovery:** [mechanizmy lifecycle](mlflow-lifecycle.md)
utrwalają niezależny audyt w PostgreSQL, wersje, aliasy i niezmienne
release pins. [Odbiór](evidence/05-03-lifecycle.md) sprawdza dwie promocje,
rollback, odrzucenie trzeciej wersji, utracone odpowiedzi oraz SIGKILL/restart
w jednorazowym Registry `retailops-demand-forecast-mechanics`.
Nie zatwierdza jakości AI 04 ani działającego runtime; pointer oznacza
zatwierdzony release. Rzeczywista kwalifikacja modelu i późniejsze wpięcie
runtime nadal wymagają odbioru. Zmiany są lokalne, bez zdalnego Required CI.

**AI 05.3c — wspólny backup/restore:** [procedura](lifecycle-backup.md)
wiąże dane aplikacji AI, metadane MLflow i pełny wolumen artefaktów w jednym
pakiecie. [Odbiór](evidence/05-03-store.md) sprawdza blokadę zapisów ról
aplikacji, SIGKILL kontrolera i jawne wznowienie, checksumy każdej tabeli,
sekwencji i całego archiwum oraz recovery niedokończonej rejestracji po
odtworzeniu do nowego projektu. Cel z częściowym restore pozostaje offline;
istniejący cel nie jest nadpisywany. Testy używają wyłącznie izolowanych
modeli mechanicznych. **AI 05.3 czeka na kwalifikację rzeczywistego modelu
AI 04.**
Nie ma jeszcze serving prognoz ani zdalnego odbioru Required CI tych zmian.

**AI 05.4a — trwała kolejka i worker:** [runbook](forecast-worker.md) opisuje
POST202/GET runów, oddzielny grant `pipeline` + `forecast:run`, scope,
przypięte release/data/policy, leasing z heartbeat, automatyczny bounded retry
i prywatne anulowanie. [Odbiór](evidence/05-04-queue.md) potwierdza rzeczywiste
HTTP/PostgreSQL/MLflow, 9 runów, 12 prób i 2 kompletne wyniki fixture,
wyścig workerów, SIGKILL po częściowym obliczeniu, odrzucenie starego tokenu,
zmianę aliasu bez zmiany pinów oraz zachowanie pełnego stanu po restarcie bazy.
**Mechanika działa lokalnie na jawnych fixture; AI 05 pozostaje otwarte.**
Implementację integracji profili i atomowej publikacji opisuje 05.6 poniżej.
Odczyt prognoz opisuje 05.7a poniżej; pozostaje odbiór qualified release’u.
Nie ma zdalnego Required CI tych zmian. AI 04 jest rozwijane w osobnej sesji;
jego nowsza kwalifikacja nie została zaimportowana do tego worktree.

**AI 05.5a — pakiet wejścia i loader release’u:** [runbook](forecast-runtime.md)
opisuje zweryfikowane features/curated, pełny grain, historię, niezmienne
profile, image/lock/schema/config pins oraz inferencję RF/HGB/baseline bez
refit. [Odbiór](evidence/05-05-runtime.md) potwierdza po 14 wartości na
rzeczywistych archiwalnych artefaktach AI 04; RF/HGB zgadzają się z adapterem
diagnostycznym. Peak RSS całej próby: ~310 MiB. **Nie załadowano rzeczywistego
zakwalifikowanego release’u i nie opublikowano prognoz.** Archiwum pozostaje
`not_ready` i ma starszy lock features niż model/runtime, więc wymaga
spójnego pakietu z AI 04 przed servingiem. Nowsze zmiany AI 04 są w osobnej
sesji. Rejestr i supervisor mają odbiór 05.5b poniżej. Do wykonania:
odbiór zatwierdzonego, spójnego modelu. Implementację integracji
z kolejką i atomowym outputem opisuje 05.6, odczyt prognoz 05.7a poniżej; zdarzenia w AI 10.
Nowa bramka kontraktów należy do `make check`, bez zdalnego Required CI.

**AI 05.5b — trwałe wejścia i ograniczony preflight:** [runbook](forecast-input-store.md)
opisuje niezmienne profile PostgreSQL, idempotentny zapis, rozdzielenie
środowisk i limity pojemności. Supervisor loadera nie przekazuje childowi
poświadczeń i ogranicza czas, pamięć oraz IO. [Odbiór](evidence/05-05-input-store.md)
potwierdza rzeczywisty pakiet, wyścig zapisów, rollback limitu JSONB oraz
SIGKILL/restart bez zmiany treści. Worker odrzucił brak zatwierdzonego release’u;
nie utworzono prognoz ani runów. Po migracji ponownie odebrano kolejkę fixtures.
Do wykonania: odbiór zakwalifikowanego, spójnego pakietu AI 04.
Implementację runu, fencing i publikacji opisuje 05.6, a odczyt prognoz 05.7a poniżej.
Zdarzenia należą do AI 10.
Brak zdalnego Required CI; AI 05 pozostaje otwarte.

**AI 05.6 — atomowa publikacja i integracja workera:** [runbook](forecast-publication.md)
opisuje przyjęcie zarejestrowanych profili, filtrowany profil wykonania,
release pins, ograniczony supervisor z heartbeat oraz jedną transakcję
partycji/manifestu/runu/historii/pointera. [Stan weryfikacji](evidence/05-06-publication.md)
zawiera testy jednostkowe na synthetic inputs i SQL-only stub gates.
Odbiór PostgreSQL potwierdza rollback częściowego zapisu, odrzucenie starego
tokenu, wygaśnięcie lease podczas publikacji, zachowanie nowszego pointera
i identyczny pełny stan po restarcie. Ponownie przeszła regresja kolejki.
Nie zaimportowano qualified release’u AI 04;
pełny odbiór rzeczywistego batchu czeka na spójny zakwalifikowany handoff.
Odczyt prognoz z paginacją i oceną freshness opisuje 05.7a poniżej; zdarzenia AI 10.
Zdalny Required CI tego brancha pozostaje nieodebrany; AI 05 jest otwarte.

**AI 05.7a — odczyt prognoz:** [runbook](forecast-read.md) opisuje
`GET /api/v1/forecasts`, oddzielne `forecast:read`, SQL scope przed limitami,
stable sort, default/max limit 50/200 i hash widoku wymagany dla dalszych stron.
Każdy odczyt weryfikuje całe manifesty/partycje oraz piny udanych runów.
[Odbiór](evidence/05-07-read.md) rozdziela testy techniczne od jakości modelu.
Starszy origin i nowsza nieudana próba dają `stale`; brak osobnego source
watermark daje `unknown`, bez deklarowania `current` na podstawie replay.
[Watermark i freshness 05.7d](forecast-freshness.md) mają osobny odbiór poniżej.
Do wykonania: spójny qualified handoff AI 04 oraz rzeczywisty
batch na jego release’ie. Zdalny Required CI nadal nie jest odebrany;
outbox/zdarzenia pozostają w AI 10. **AI 05 pozostaje otwarte.**

**AI 05.7b — katalog modeli i wersji:** [runbook](model-catalog.md) opisuje
trzy endpointy `/models`, szczegół modelu i `/versions`. Katalog obejmuje
wersje z publikacją w autoryzowanym scope; filtr SQL poprzedza distinct/count
oraz limit 1000 wersji. Numeric sort, piny enrollment/release i hash paginacji
chronią odczyt. Nie udostępnia globalnych metryk ani URI plików.
Head spoza scope pozostaje ukryty, aliases/runtime/drift nie są potwierdzane,
freshness pozostaje `unknown`. [Odbiór](evidence/05-07-catalog.md) podaje
rzeczywisty HTTP/PostgreSQL, próby uszkodzenia i restartu oraz granice fixture.
Scoped historyczne evaluations opisuje 05.7c poniżej, watermark 05.7d.
Qualified batch AI 04 nadal pozostaje do wykonania.
Zdalny Required CI tego brancha nie jest odebrany. **AI 05 pozostaje otwarte.**

**AI 05.7c — historyczne oceny:** [runbook](evaluations.md) opisuje listę i szczegół
`/evaluations`, całkowite pokrycie scope raportu przez grant, filtr statusu,
immutable import i stabilne strony. Metryki MAE/WAPE zostały odtworzone z
zapisanych predykcji/etykiet kalkulatorem AI 04, bez treningu. Pierwotny status
jakości oraz stare code/lock pins są zachowane, bez nowej kwalifikacji modelu.
[Odbiór](evidence/05-07-evaluations.md) obejmuje HTTP/PostgreSQL, 37 synthetic
ocen i rzeczywisty historyczny export (12012 memberships), odmowę częściowego
scope, niezmienność i SIGKILL/restart. Tabela pochodzi z migracji
`0013_forecast_evaluations`; bieżący DB head to `0017_v12_outputs` opisane powyżej.
Trwałego stosu nie zmieniano. Pozostają qualified handoff AI 04,
batch na jego release'ie oraz zdalny Required CI. **AI 05 pozostaje otwarte.**

**AI 05.7d — watermark i świeżość:** [runbook](forecast-freshness.md) opisuje
przypiętą deklarację kompletności źródła, osobną dostępność obserwacji w cutoff,
wejścia/outputy 1.1 oraz odczyt `forecast-read-v2`. Origin ma maksimum 24 h,
watermark musi obejmować origin, a obserwacja może mieć lag do 1 dnia zgodnie
z dobowym close. Starsze artefakty 1.0 zachowują canonical IDs i nie otrzymują
domyślnego `current`. [Odbiór](evidence/05-07-freshness.md) obejmuje rzeczywisty
HTTP/PostgreSQL, mixed-version odczyt, atomową odmowę rehashed niezgodnego dowodu,
tamper/restore i SIGKILL/restart. Przygotowanie 1.1 ze zweryfikowanego archiwum
AI 04 zachowało parenty i stare locki; nie kwalifikowało modelu ani nie publikowało
prognoz z tego archiwum. Migracja freshness to `0014_forecast_freshness`;
**bieżący head to `0017_v12_outputs`** opisane powyżej;
trwałego stosu nie migrowano. Pozostają qualified handoff AI 04, rzeczywisty
batch na jego release'ie oraz zdalny Required CI. **AI 05 pozostaje otwarte.**

**AI 03.3 — kontrakt handoff odebrany lokalnie:** [snapshot źródła](source-snapshot-handoff.md)
ma wspólną wersję 1.0.0, pełny mały fixture oraz niezależną walidację schema,
identity i transportu bez generatora/DB.
**AI 03.4 — typed importer odebrany lokalnie:** [CLI i runbook](source-snapshot-import.md)
opisują pełną weryfikację Parquet, canonical hashes, gates, atomową publikację
i niezmienny reimport. [Evidence](evidence/03-04-importer.md) podaje testy obu
smoke, partycje, truth opt-in i odłączony wheel.
**AI 03.5 — curated:** [runbook](curated.md) opisuje jawne mappings,
normalizację, quarantine, immutable IDs i odczyt z pełnej historii wersji.
[Evidence](evidence/03-05-curated.md) podaje 743 testy i pomiary smoke/as-of.
[AI 03.6 — bramka cross-repo](evidence/03-06-cross-repo.md) wiąże oba repo
przez pełne smoke i przypięte rewizje. Końcowy odbiór RetailOps określa wejście
do 04/06 oraz odrębne readiness use cases.
AI 12 jest wznowiony na `ai/12-resume`, z aktualnym `main` i zachowanym kodem
`ai/12-tools`. Integracja i ponowna kwalifikacja trwają; historyczne wyniki poniżej
nie potwierdzają obecnej wersji. AI 03 został zaakceptowany w [PR #5](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/5).
Przypięte wyniki Required CI, testów i rzeczywistego Compose/persistence
znajdują się w [końcowym evidence cross-repo](evidence/03-06-cross-repo.md).

## Etap 06 — inventory

**AI 06 ma [końcowy odbiór](evidence/06-inventory-complete.md).**
[Snapshot/import/curated 1.1](reference/inventory-snapshot-11.md) obsługuje
source 2.7, 43 facts/plans i oddzielne private evaluation truth. Ledger, historyczny
routing, sprzedaż/zwroty, orders/plans/receipts i snapshots są niezależnie uzgadniane.
Curated zachowuje causal availability, fizyczny grain i odczyty as-of bez future fallback.
Pełny pipeline obu profili dwukrotnie spełnia budżet 300 s / 1024 MiB.
Inventory readiness dotyczy danych; modele 04/05/08 wymagają własnej oceny.
AI 04 i AI 12 zachowują odrębne branche/worktrees.

## Etap 11

- Zatwierdzony korpus: 29 dokumentów, 451 fragmentów, przypięte źródła Git,
  statusy, klasy dostępu i dokładne cytaty.
- Amazon Titan Text Embeddings V2, 1024 wymiary, `eu-north-1`, kontekst
  nagłówków i wersjonowany cache. Wywołania AWS są jawne i ograniczone budżetem.
- Golden set: 44 pytania, w tym 9 krytycznych. Bez zmiany etykiet i progów:
  **Recall@5 0,852941 ≥ 0,80; MRR 0,661275 ≥ 0,60; cytaty i krytyczne 1,0**.
- Trwałe runy odtwarzają pomiar bez AWS. Niezaliczony próg zachowuje raport
  `failed/gate_failed`, bez outputu. Sukces nie aktywuje indeksu automatycznie.
- Użytkowa kwalifikacja wymaga udanego runa, zgód, raportu jakości i kompletnego
  przeglądu podobieństw. Aktywacja i rollback mają CAS, idempotencję i niezmienne piny.
- Właściwy indeks jest aktywny w lokalnej bazie w kanale `retrieval`.
  PostgreSQL odtworzył wszystkie 44 wyniki golden. Runtime wymaga jawnego
  `RAG_BEDROCK_ENABLED=true` i poświadczeń AWS procesu.
- 643 testy regresji; dodatkowa kontrola 77 testów po dopracowaniu current/report
  również przechodzi. Historyczny odbiór semantyczny obejmował migrację `0008_rag_semantic`,
  pgvector, HTTP, runy, SQL gates, aktywację/rollback, SIGKILL i trwałość danych.

Nie pozostały otwarte blokady implementacji lub jakości Etapu 11.
Fake pozostaje wyłącznie ścieżką testową i nigdy nie uprawnia do użytkowej aktywacji.
Szczegółowe wcześniejsze evidence opisuje historyczne, mniejsze zakresy odbioru;
nie stanowi bieżącej listy braków.

## Rozpoczęty AI 12

[Pierwszy zakres](agent-tools.md) obejmuje osiem narzędzi tylko do odczytu,
typowane request/result schemas, principal z prywatnych poświadczeń, jawne
`assistant:query` i prawa poszczególnych źródeł, kontrolę całego scope oraz
wspólny budżet wywołań. Adapter wiedzy korzysta z jednego serwerowego pinu
AI 11. Przyszłe narzędzia biznesowe mają ścisłe kontrakty i testowe odpowiedniki;
brak rzeczywistego źródła jest jawnym `unavailable`.
[Odbiór](evidence/12-tools.md) opisuje lokalne testy i granice.
Zakresy AI 12 są zapisywane lokalnie na osobnym branchu `ai/12-tools`.

[Drugi zakres](agent-chat.md) dodaje konfigurację modelu/budżetu, sześć
wersjonowanych promptów, checksum całej konfiguracji i schematów, kontrolę
CLI offline oraz scripted fake chat. Sesja modelu dzieli deadline z narzędziami;
retry i jedna naprawa zużywają wspólny budżet tokenów/kosztu. Draft przechodzi
kontrolę zakresu planowanych narzędzi oraz powiązania referencji, cytatów,
as-of i freshness z rzeczywiście pobranymi wynikami.
[Odbiór](evidence/12-chat.md) opisuje testy i granice tego przygotowania.

[Trzeci zakres](agent-graph.md) dodaje acykliczny LangGraph, jedno dogranie
brakujących danych i jedną wspólną naprawę, dokładne wywołania z typed requestu,
katalog kanonicznych faktów i sprawdzalne porównanie okresów sprzedaży.
Podmienione liczby, jednostki, okresy, swobodne wnioski, braki dowodów,
konfliktujące prognozy i niejednoznaczny mapping mają kontrolowane wyniki.
Bezpieczny trace ma odczyt właściciela, scope, retencję i limit w pamięci.
[Odbiór](evidence/12-graph.md) podaje testy i granice profilu.

[Czwarty zakres](agent-evaluation.md) dodaje deterministyczną politykę trzech
kandydatów do przeglądu przez człowieka, wiązanie do wybranych faktów i expiry.
Wersjonowany golden obejmuje 50 przypadków, w tym sześć pytań z AI 11,
niezależnie zapisane oracles i scripted replies. Release wiąże config grafu,
politykę/prompty v4, schemas, golden, ewaluator i lock. `make agent-evaluate`
jest bramką `make check`; pomiar dotyczy jawnych fixtures i kanonicznych twierdzeń.
[Odbiór](evidence/12-evaluation.md) podaje wyniki oraz ograniczenia.

[Piąty zakres](assistant-api.md) dodaje kontrakt HTTP queries/runs, lokalne
auth i kontrolę całego scope, trwały zapis run/odpowiedź/review candidate
w bazie AI oraz wspólne admission PostgreSQL. Trace sprawdza również cofnięte
prawa narzędzi/wiedzy; admin wymaga osobnego assistant:audit.
[Odbiór](evidence/12-assistant.md) opisuje rzeczywiste próby PG/HTTP i granice.
Standardowe serve obsługuje opcjonalny runtime dokumentacyjny opisany niżej;
bez jego konfiguracji query daje 503. Fake jest dostępny wyłącznie w testach.

[Chat Bedrock](agent-bedrock.md) ma adapter Converse/CountTokens, kontrolę
formularza/dostępu konta, zweryfikowane profile EU, circuit breaker i ograniczony
smoke. Formularz oraz aktywacja Haiku 4.5 i Sonnet 4.6 zostały wykonane po
potwierdzeniu danych projektu osobistego. Zgoda kosztowa wynosi **1,50 USD
łącznie**; [rejestr wszystkich prób](evidence/12-bedrock-budget.json) zachowuje
**1,4017210 USD** szacunków/rezerw, w tym pełny cap przerwanej próby.

[Poprzedni mieszany test Sonnet](evidence/12-bedrock-real.md): **5/6**, w tym
3/3 przypadki biznesowe i 2/2 zabezpieczenia serwera. Historyczny przypadek dokumentacji
dał `invalid_evidence`: fixture nie zawierał odpowiedzi, chociaż etykieta
wymagała `answered`. Wynik i koszt tej próby pozostają zachowane.
[Bieżąca poprawka dowodów dokumentowych](agent-document-evidence.md) wprowadza
`typed-facts-v2`, prompty v4 oraz golden v2. Pytanie musi mieć jawne wymagania
i pobrane źródła pokrywające każde z nich; sam cytat, score lub status
`verified` nie wystarcza. Sześć oryginalnych pytań ma poprawione fixtures/etykiety
opisane w [przeglądzie](evidence/12-document-label-review.json).
[Ponowny test Sonnet](evidence/12-bedrock-runs/sonnet-4-documents.json)
zaliczył **6/6 pytań dokumentacji**, bez napraw: pięć kompletnych odpowiedzi
i jedno poprawne `insufficient_evidence`. Koszt szacowany wyniósł
**0,2439129 USD**. Aktualny stan budżetu uwzględnia też późniejszy test runtime.
[Odbiór poprawki](evidence/12-document-evidence.md) zawiera pełne wyniki.
Sonnet zaliczył ten ograniczony test; pełny golden rzeczywistego
modelu i retrieval nadal wymaga odbioru.
Test używa rzeczywistego chatu i syntetycznych narzędzi/retrieval.

[Runtime dokumentacyjny](assistant-document-runtime.md) podłącza standardowe
API do dwóch jawnych tras pytań, zweryfikowanego importu AI 03, rzeczywistego
PostgreSQL/pgvector, Titan i Sonnet. Resolver zachowuje source UUID i sprawdza
przypisanie kanału w całym okresie. Przypięte konfiguracje, brak automatycznej
zmiany indeksu i kontrola pełnych dowodów pozostają obowiązkowe.
[Ponowny odbiór](evidence/12-bedrock-runs/sonnet-6-runtime.json): **2/2**,
4 Converse + 2 embeddings, bez napraw; odpowiedzi i trace zapisane w bazie,
obcy operator nie ma dostępu. Projekcja sprawdzonych faktów ogranicza kontekst
wysyłany do modelu. [Evidence zakresu](evidence/12-document-runtime.md) podaje
regresję, testy SQL, pierwotną nieudaną próbę i ograniczenia.
Pozostaje **0,0982790 USD** zatwierdzonego budżetu; dalszy większy test wymaga
nowej zgody. Kwoty są szacunkami/rezerwami, nie rachunkiem AWS.

Następny zakres bez AI 10: rozszerzenie zbioru pytań/routingu i kwalifikacja
rzeczywistego modelu oraz retrieval na pełnym golden. Obecne dwie trasy nie
są ogólnym plannerem języka naturalnego. Resolver source działa na przyjętym
syntetycznym fixture AI 03; adaptery rzeczywistych źródeł biznesowych i ML
pozostają do podłączenia. Pełne AI 12 wymaga AI 10 i E2E
sugestii/outbox/v2/read API/UI. Profil sprawdza kanoniczne fakty i literalne
cytaty, bez deklaracji jakości swobodnych odpowiedzi. Etykiety i progi są
lokalnym profilem developmentu, bez niezależnego business/model approval.

## Fundament i dalsza praca

Etap 01 ma odbiór lokalny i zdalny: pakiet/CLI, settings, HTTP/telemetry,
lokalne poświadczenia i scope, odrębne PostgreSQL AI/pgvector i MLflow,
wykonywalne kontrakty danych/run/tool, jawne migracje i Required CI.
[Uruchomienie](local-stack.md), [uprawnienia](access-control.md),
[kontrakty](data-contracts.md), [odbiór zdalny](evidence/01-remote-ci.md).

[Bieżący odbiór danych](evidence/03-06-cross-repo.md) jest wspólny z RetailOps.
Po pełnej bramce 03 można rozdzielić forecasting **04** w AI i ledger **06**
w RetailOps; nowe źródło po 06 wymaga ponownego importu i zależnych ocen. Równolegle można przygotować
[rozwijany runtime **AI 12**](assistant-api.md). Pełne zamknięcie agenta wymaga **AI 10 i 11**;
11 jest gotowy, 10 nadal należy do późniejszego ciągu danych/ML/integracji.
[Pisemna mapa etapów i repozytoriów](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/kolejnosc-i-repozytoria.md).

## Granice

Odbiór dotyczy lokalnego retrieval na konkretnym zatwierdzonym snapshotcie.
Zmiana dokumentacji na `main` nie aktualizuje automatycznie korpusu. Kolejna
wersja wymaga nowego snapshotu, przeglądu i ewaluacji.
Generowanie swobodnych odpowiedzi, ewaluacja rzeczywistego modelu i pełna
ścieżka agenta pozostają do realizacji w AI 12. Pipeline danych, modele, integracja zdarzeń oraz
wdrożenie AWS/EKS mają dalsze bramki. Limit AWS na proces nie zastępuje wspólnego
budżetu wielu replik ani produkcyjnego IAM. Nie deklarujemy wdrożenia w chmurze.

## AI07 — pełny strumień operacyjny DQ

[Odbiór konsumenta v2](evidence/ai/07/07.5-full-dq-consumer/README.md) rozwija
wcześniejszy replay wybranych sprzedaży do wszystkich sprzedaży i natywnych
zgłoszeń zwrotów, z ogonem po historii sprzedaży. Brakujące fakty pozostają
jawne; odrzucone zwroty nie zwiększają refundowanych sztuk. Pokrycie konkretnego
źródła nie kwalifikuje kompletności dnia. [Kwalifikacja dni](reference/day-qualification.md)
weryfikuje jawne zamknięcie źródła i dostępne wtedy zaakceptowane fakty DQ.
[Cechy anomalii](reference/qualified-anomaly-inputs.md) korzystają z tej bramki
osobno dla historii przed ocenianym dniem i wyniku po jego zamknięciu.
[Detektory](reference/anomaly-detectors.md) dodają podział czasowy z cutoffami,
baseline/Isolation Forest i progi wyznaczane z walidacji. Artefakty odtwarzają
trening i wynik; nie kwalifikują jeszcze jakości modelu. AI07 wymaga oceny
obserwacji i epizodów na większych danych, porównania modeli oraz własnego lifecycle.

## 2026-10-07 — wznowienie AI12

AI12 pozostaje **in_progress**. Branch `ai/12-resume` łączy zachowany kod
`ai/12-tools` z `main` na `3329a815`. Konflikty API, uprawnień, zależności
i migracji zostały rozwiązane bez edycji worktree AI09/AI10. Nowy profil
26 pytań / 12 intencji jest proponowany, ograniczony do kwalifikacji offline.
[Zakres i odbiór](evidence/12-resume.md), [routing](assistant-routing.md).

Do READY nadal potrzeba rzeczywistych adapterów biznesowych/ML, obsługi
przepływu sugestii AI12 → outbox/v2 → API/UI przez AI10, kwalifikacji Sonnet
z rzeczywistymi danymi i przeglądu etykiet/konfiguracji. Wyniki historyczne
nie potwierdzają nowego code hash.

[Poprawki zgodności CI](evidence/12-ci-remediation.md) aktualizują osobny
lockfile TensorFlow, weryfikują oba niezmienione modele anomaly przy jawnym
rozszerzeniu środowiska o LangGraph i doprecyzowują środowisko historycznych
testowych release'ów prognoz. Lokalnie 569/569 wybranych testów oraz pełne
`ci-checks` passed. Nowe manifesty `.resume.v2` wiążą bieżący kod; zdalne
OCI/PostgreSQL/MLflow i TensorFlow wymagają wyniku dla aktualnego head PR32.
