# 09.13 — fizyczny eksport pięciu ról prognoz

AI 09 pozostaje **in_progress / not_ready**. [Receipt](09-13-physical-forecast-export.json)
i [runbook](../physical-forecast-export.md) opisują rzeczywisty kontrolny eksport
na native źródle 1.1 po zamknięciu AI 07–08. Nie wykonano nowego fitu projektu,
nie inicjalizowano nowej kampanii i nie otwarto końcowego portfolio.

## Wykonany przepływ i kontrole

Wspólny rdzeń odtwarza typed snapshot i całe curated w jednej prywatnej kopii.
Nowy eksporter buduje z niej istniejące cechy/historię, waliduje pełne inventory
obserwacji i wersji, kwalifikuje każdy klucz według roli i zapisuje sześć plików,
łącznie z purged. Brak/closed/censored oraz powody wyłączenia pozostają w coverage.
Limity i runtime są sprawdzane przed parent I/O; także zmiana pożyczonych metadanych
przerywa cały odczyt i trwale rozlicza rezerwacje jako failed.

**119 testów regresji przeszło w 372.37 s**. Kontrole odłączonego wheela mają
**22 passed w 41.92 s**. Pięć dodatkowych testów rzeczywistych plików i SQLite
odrzuca resealed feature hash, duplikat, zmienione eligibility, rolę i checksum;
kontrolowane feature/label deklaracje w tych testach nie są dowodem źródła.
Pełne checks, pakowanie i Compose config przeszły; affected mypy/lint po ostatnim
guardzie także przeszły. Końcowy SHA nadal wymaga własnego pełnego zdalnego CI. Lokalny skan przed
publikacją rozpoznał sześć SHA-256 populacji i opisowy tekst jako generic-api-key;
zmieniono nazwy pól receiptu, zachowując wartości i reguły skanera. Oryginalne
niezaliczone logi pozostają prywatnym dowodem; commit nie był opublikowany.
Pełna historia publikowanego commita jest sprawdzana oddzielnie od cudzych
gałęzi obecnych we wspólnym repozytorium lokalnym.

## Rzeczywiste dane diagnostyczne

Native producent `1de4627` wygenerował jawny ai-load 173/4/2/2, seed 42, do
2026-07-31. Publiczny snapshot 1.1 i curated mają 29285 wierszy. Eksporter ma
14560 pełnych kluczy: train 3360, cztery role po 1120 i purged 6720. W train 2568
wierszy jest eligible; także 648 closed i 168 insufficient history pozostają
w populacji. Powody mogą się nakładać. Wagi/metryki portfolio nie są tu oceniane.

Aktualny native i installed-wheel mają identyczne 497 modułów, runtime,
manifest oraz digests wszystkich ról. Pomiary wall/process peak są w receipt.
Nie zmierzono całego drzewa procesu ani pełnego ai-training. Mniejszego
kontrolnego profilu nie nazwano canonical ai-training. Pierwsze błędy katalogu
wyjściowego i konstrukcji testów zachowano; generacji nie powtarzano.

## Publikacja wcześniejszej integracji

[PR29](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/29)
ma [14 zielonych jobów](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37588436833)
dla dokładnego `4a0a6b5` i został scalony jako `5216e31`. PR18 został automatycznie
zamknięty jako merged. Wcześniejszy failed required-result anulowanego push-run
pozostaje w historii; nie zmieniano ochrony gałęzi i nie nadpisywano checków.
[Required CI merge/main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37592860960)
ma komplet 14 zielonych jobów na dokładnym merge/main `5216e31`. Bieżący eksporter wymaga osobnego
commita/PR i CI. Nie jest jeszcze odebranym, audytowanym runnerem kampanii.

## Pozostały pełny zakres

Publiczne spięcie z nowym dziennikiem i zamrożonymi rzeczywistymi recepturami,
odrębna końcowa rola, pomiar canonical ai-dev/ai-training oraz pełny odbiór 1.2,
fair fit/kalibracja, końcowe trzy seedy, robustness/segmenty/niepewność/koszty,
trzy raporty/karty, MLflow/lifecycle oraz dokładny pełny odbiór main.
