# AI 09 — kalibracja po zamrożonym wyborze na Tune

`evaluation_campaign.campaign_calibration.fit_campaign_forecast_calibration`
wykonuje osobny plan [v18](../contracts/evaluation/v18/campaign_forecast_calibration_plan.schema.json)
jako `calibrator_fit` wyłącznie na development `calibration`. Trwała rezerwacja
poprzedza odczyt rodziców i etykiet. Zakończony Tune jest prerekwizytem każdego
raw scoring Calibration, a zakończone scoringi i Tune są prerekwizytami kalibracji.
Nie można dopisać tej kolejności dopiero po odczycie wyników.

Runner sprawdza audytowany eksport, ukończony Tune i pełne bundle raw scoring.
Każdy triplet RF/HGB/TensorFlow z Tune musi wystąpić dokładnie raz również na
Calibration, z tymi samymi operation IDs fitów, receiptami i hashami modeli.
Populacja Calibration jest wspólna między próbami. Mediana wybrana na Tune
przypina konkretny triplet; kalibracja nie wybiera innej architektury lub próby.
Przy wyborze bazowej mediany używa pierwszego prerejestrowanego tripletu.
Średnia pozostaje zgodna z osobnym wyborem Tune. RF nie otrzymuje mediany.

Świeży worker otwiera wyłącznie `calibration.jsonl` i wybrany plik prognoz.
Wykonuje jeden pełny odczyt, sprawdzając wszystkie klucze, kolejność, eligibility,
wykluczenia, checksum każdego example oraz hashe i liczebności całych plików.
Nie otwiera Tune, independent development ani final outcomes. Nie dopasowuje
ponownie modelu lub preprocessingu.

Dla każdego horyzontu 1–14 zapisuje bezwzględne błędy wybranej mediany w osobnym
indeksie SQLite na dysku. Cache ma 4 MiB, liczba stron jest limitowana, a scratch,
czas, RSS własnego drzewa i rezerwy obejmuje supervisor. Promień to dokładna
statystyka pozycyjna `ceil((n+1)*0.9)`, liczona całkowitoliczbowo. Nie przycinamy
niedostępnej pozycji do ostatniego błędu ani nie zastępujemy brakującego horyzontu
wspólną pulą. Każdy horyzont wymaga co najmniej 100 eligible obserwacji oraz
80% eligibility coverage. Brak któregokolwiek warunku daje `not_ready` całego
kalibratora; wszystkie wykluczenia i liczebności pozostają w dowodzie.

Zamrożony przedział ma granice `max(0, median-radius)` i `median+radius`.
Jego późniejsze zastosowanie nie czyta etykiet i nie wykonuje refitu.
Nominalne 90% nie oznacza gwarancji pokrycia zależnych danych czasowych:
rzeczywiste coverage, bias i segment gates trzeba ocenić na niezależnej roli,
a następnie na zamrożonym final test.

Bundle zawiera plan, pełne parent bindings, populację i typowany kalibrator.
Prywatny residual index pozostaje w scratch próby. MLflow korzysta z prywatnego
FileStore i jawnego katalogu artefaktów, tak jak zaakceptowany fitting/scoring;
zapisuje parametry, artefakty i zmierzone koszty. Porażka zachowuje próbę i koszt,
bez automatycznego retry. Fsync i trwały receipt poprzedzają journal completion.
`verify_campaign_forecast_calibration` sprawdza ukończenie, hashe, kontekst,
liczebności, rangi i wynik bez nowego odczytu etykiet. Nie wyznacza ponownie
promieni: ich dowodem jest wykonanie przypiętego, mierzonego workera.

[Evidence przygotowania](evidence/09-29-campaign-calibration-preparation.json)
oddziela kontrolowane testy od projektu. 198 testów integracji kalibracji,
Tune, raw scoring, fit, generation i export przeszło w 90.77 s. Testy danych
używają rzeczywistych plików ról; testy kolejności mają jawnie mocked fit/scoring
workers. Worker controls mają deklarowanego rodzica Tune i mocked gate wersji.
Wymagany native test uruchamia świeży worker i rzeczywisty MLflow na małych
plikach Calibration; jest dodany do ośmiotestowego `make tensorflow-check`.
Lokalnie wykonano tylko collection, aby zachować rezerwy otwartych sesji.
Native job `113023848619` na `c686ca97` zaliczył 8 testów w 135.72 s.
Pełny Required CI `37688813627` zakończył się failure: 15/17 success.
Scoring matematyczny i native CPU przeszły, ale dotychczasowy manifest partycji
z pełnym przypięciem 567 modułów miał 65 716 bajtów i przekroczył techniczne
64 KiB. Lokalna regresja odtworzyła ten błąd. Bounded metadata cap wynosi teraz
128 KiB; wszystkie hashe, schematy i limity profili oraz zasoby kampanii pozostają.
158 testów partycji/outcome/journal przeszło w 90.14 s, a dodatkowa kontrola
odrzuca zbyt duży manifest przed feature I/O i przed publikacją.
Oryginalny wynik oraz koszty są zachowane. Poprawiony head wymaga własnego
pełnego CI i native CPU. [PR #42](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/42)
zawiera także aktualny main `92c2a3cd`: Required CI `37689831146`
ma 17/17 success, w tym required-result. Poprawiony head kalibracji
wymaga osobnego pełnego odbioru przed protected merge.

CI `37694343795` na `0796d27e` ujawniło dodatkową porażkę testu przerwanego
backupu w jobie `113042573697`: `backup_interruption_not_observed`.
Cały run zakończył się failure z 15/17 success: persistence i required-result
nie przeszły. Wszystkie shardy testów, w tym regresje limitu manifestu,
przeszły; native CPU `113042573734` zaliczył 8 testów w 112.19 s.
Dotychczasowy komunikat nie pokazywał kodu wyjścia dziecka ani przyczyny
wewnętrznej. Nowa kontrola zachowuje rzeczywisty SIGKILL, maintenance,
fence obu baz i odrzucenie połączeń aplikacyjnych przed jawnym resume.
Świeży proces stosuje ten sam acceptance Compose overlay co rodzic.
Porażka podaje tylko bounded, allowlisted kody, klasę wyjątku i pozycje
we własnym kodzie. Nie wypisuje treści poleceń, tracebacków, locals ani
credentials. Przyczyna awarii bazy nie jest jeszcze potwierdzona.
78 lokalnych testów backupu, przerwania i cleanup przeszło w 2.38 s;
kontrola OS rzeczywiście zabiła wyłącznie nowy własny proces, z mocked
operacjami bazy. To nie jest native PostgreSQL acceptance. Mypy dla 688
plików, Ruff i format przeszły. Nie uruchamiano lokalnie Dockera.
Poprawiony head nadal wymaga pełnego CI przed scaleniem.

Journal projektu nadal nie jest zainicjalizowany, nowe projektowe fity wynoszą
zero, projektowa kalibracja nie jest wykonana i final test pozostaje zamknięty.
Do AI 09 ready pozostają pełne profile, projektowy wybór i kalibracja,
niezależna ocena trzech zastosowań, końcowe seedy 42/137/2026, scenariusze,
segmenty, niepewność i koszty, lifecycle, trzy karty/raporty/runbooki oraz
chroniona publikacja wszystkich zmian i pełne CI końcowego main.
