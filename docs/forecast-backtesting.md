# Chronologiczny backtesting AI 04.6

Backtest korzysta z tego samego [splitu i etykiet](forecast-manifests.md),
[baseline'ów/evaluatora](forecast-baselines.md) i [modeli RF/HGB](forecast-models.md).
Orkiestracja wyznacza kolejne okna, dopasowuje preprocessing i oba modele
osobno w każdym foldzie, sprawdza granice wiedzy i zbiera raport łączny.
Nie wprowadza drugiego silnika predykcji lub metryk.

## Zamrożony plan

[Konfiguracja](../contracts/forecast/v1/backtest.default.json) określa trzy
foldy `expanding`, początkowo 6 dni treningu, 6 dni walidacji, 6 dni
development holdoutu i przesunięcie o 6 dni. Trening rozszerza się do
6/12/18 dni. Wariant `rolling` zachowuje stałe okno `initial_train_days`,
przesuwając również jego początek. Zmiana wariantu/granic zmienia IDs.

Między train/validation oraz validation/holdout jest **15 pełnych dni purge**:
14 dni horyzontu i dzień na dostępność etykiety w tym profilu. Training
cutoff zamyka dzień poprzedzający pierwszy origin walidacji; selection cutoff
poprzedza pierwszy origin holdoutu. Evaluation cutoff ma 15 dni ogona po
ostatnim origin holdoutu. To jawne cutoffy: późniejsze etykiety są cenzurowane,
a nie włączane dzięki założeniu o opóźnieniu. Minimalne coverage pozostaje
bramką strukturalną; minimalne liczebności jakościowe należą do AI 04.7.

Domyślny kalendarz temporalny 2026-05-19…2026-07-17 daje:

| Fold | Train origins | Validation origins | Development holdout origins | Training cutoff | Selection cutoff | Evaluation cutoff |
|---|---|---|---|---|---|---|
| expanding-01 | 19–24 maja | 9–14 czerwca | 30 czerwca–5 lipca | 8 czerwca | 29 czerwca | 20 lipca |
| expanding-02 | 19–30 maja | 15–20 czerwca | 6–11 lipca | 14 czerwca | 5 lipca | 26 lipca |
| expanding-03 | 19 maja–5 czerwca | 21–26 czerwca | 12–17 lipca | 20 czerwca | 11 lipca | 1 sierpnia |

Wszystkie cutoffy to **23:59:59 UTC**. Plan jest wyliczany z jawnego kalendarza
przed odczytem outcomes. Zbyt krótki kalendarz blokuje run; okna nie skracają
się automatycznie. Originy niewykorzystane w danym foldzie pozostają jako
`purged`. Każdy fold ma tę samą listę memberships dla pięciu metod.

Walidacje różnych foldów są rozłączne; tak samo ich holdouty. `step_days`
mniejsze od długości tych okien są odrzucane, aby nie liczyć ponownie tego
samego grainu w agregacie roli. Target dates mogą się powtarzać dla różnych
originów/horyzontów: są to odrębne decyzje prognozowe z pełnym grainem.
Nie interpretujemy ich jako niezależnych zdarzeń sprzedaży.

Historyczne obserwacje znane przy późniejszym origin mogą zasilać jego lagi,
także gdy wcześniej należały do ocenianego okresu. Train używa wyłącznie
eligible labels dostępnych do training cutoff własnego folda. Parametry
modeli pozostają zamrożone, bez dostrajania na późniejszych wynikach.
Predykcje train pozostają `in_sample_diagnostic`; raport łączny ich nie używa.
Własny adapter nie pozwala prognozować przed granicą wiedzy treningu.

## Wybór i raport łączny

Każdy fold osobno wybiera metodę według validation MAE z dotychczasowym
kryterium poprawy >5% wobec najlepszego baseline'u. Ten wybór jest przypięty
przed oceną jego holdoutu. Raport pokazuje wszystkie pięć metod i dodatkowo
`validation_selected`: strategię używającą w każdym foldzie jego zamrożonego
wyboru. Nie wybiera nowego championa na podstawie wyników holdoutu.

Pooled MAE dzieli sumę błędów bezwzględnych przez sumę eligible rows.
Pooled WAPE dzieli sumę tych błędów przez sumę bezwzględnych actuals.
Nie uśredniamy WAPE foldów. Zera pozostają w liczniku błędów, a zerowy
mianownik daje null. Brak wymaganej predykcji daje `incomplete` bez
częściowych metryk. Pusty wymagany fold nie znika w agregacie pozostałych:
blokuje podsumowanie jako `selection_not_ready`.

Leakage report wiąże granice foldów, najpóźniejszą dostępną etykietę treningu,
train labels hash, model IDs, liczebności i hash wspólnego grainu. Sprawdza
rzeczywiste rekordy, nie deklaracje konfiguracji. Każdy membership ma
rekord wszystkich pięciu metod z identycznym eligibility i wyłączeniami;
model nie może ukryć trudnego przypadku przez osobne pominięcie klucza.

## Polecenia i artefakty

```bash
uv run --locked --extra snapshot --extra forecast retailops-ai-forecast backtest-run \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --curated-dir data/generated/curated/<curated_dataset_id> \
  --config contracts/forecast/v1/backtest.default.json
uv run --locked --extra snapshot --extra forecast retailops-ai-forecast backtest-verify \
  --backtest-dir data/generated/forecast-backtests/<backtest_id> \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --curated-dir data/generated/curated/<curated_dataset_id>
make forecast-backtest-check
```

Exit 0 oznacza poprawne wykonanie protokołu, 3 — zapisany `not_ready`,
2 — odrzucone wejście/konfigurację. Domyślny gate CI sprawdza planner i
agregację na komponentach. Test integracyjny uruchamia pełne dwa małe foldy.
Checker z `--feature-dir` i `--curated-dir` uruchamia pełny temporalny
backtest, niezależny source rebuild/training/replay i immutable rerun.

Katalog `forecast-backtest-sha256-…` zawiera manifest, config, model card,
leakage report, pooled metrics, pełny `split/` oraz `comparison/` z wszystkimi
pipeline'ami, predykcjami, coverage, wyborami i metrykami per fold.
Rodzice pozostają immutable. ID wiąże plan, source/feature/split/labels,
comparison descriptor, wyniki i kod/runtime. Ścieżki, timestampy i pomiary
zasobów nie zmieniają logicznej tożsamości. Rerun zachowuje oryginalne bajty.

`backtest-verify` ponownie buduje etykiety/split z właściwego curated,
uczy modele każdego folda i odtwarza raport. Samo przeliczenie hashy
zmienionych metryk/etykiet/drzew nie zastępuje takiej weryfikacji.
Trening zachowuje limity per proces i macierzy z AI 04.5, maksymalnie
10 foldów. Dane, predykcje i indeksy na dysku mają dotychczasowe limity;
audyt używa dodatkowego ograniczonego indeksu SQLite, bez pełnego panelu w RAM.

## Granice odbioru

To **development backtest** małego syntetycznego profilu. Dane tego snapshotu
były już używane w wcześniejszym development evidence; nie przedstawiamy
nowych okien jako nietkniętego final testu. Portfolio dataset/final test
zostaną zamrożone osobno przed kampanią AI 09. Nie otwierano ich tutaj.

Observed sales może być ograniczona zapasem. Inventory/truth features są
wyłączone; odrzucenie wcześniejszego RF w RetailOps pozostaje w mocy.
Nie ma wdrożenia, AWS, DB/API ani promocji. Model nadal ma `not_ready`.
[Odbiór 04.6](evidence/04-06-backtesting.md) podaje konkretne wyniki i kontrole.
Pozostają **AI 04.7 — przekroje, bias, niepewność i quality gates** oraz
**AI 04.8 — lifecycle/handoff**.
