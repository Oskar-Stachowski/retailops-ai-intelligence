# AI 04 — finalna wersja v12

Stan na 2026-10-01: **v12 wybrana jako finalna wersja developerska**.
AI 04 ma decyzję odbioru `ready` z trzema zaakceptowanymi odstępstwami.
[Decyzja właściciela](evidence/04-v12-acceptance.md) obowiązuje po chronionym
merge i zielonym Required CI. Oryginalna kwalifikacja modelu pozostaje
`not_ready`: **221 passed / 3 failed**. Jest zachowana bez zmiany bajtów.

## Pełny wynik

Kampania obejmuje **64/64 wcześniej zaplanowane kohorty**, seedy 720001–720064
i **27 396 096** wierszy prognoz. Oceniono wszystkie 224 wymagane przekroje.
Trzy niezaliczone oceny dotyczą MSE średniej w development holdout:

| Fold | Kategoria | MSE baseline → v12 | Pogorszenie MSE |
| --- | --- | --- | --- |
| rolling-01 | `655e5f7a-c40d-5c55-b071-175c1b2d6502` | 75,923598 → 76,079042 | +0,204738% |
| rolling-03 | `655e5f7a-c40d-5c55-b071-175c1b2d6502` | 63,435173 → 63,435566 | +0,000619% |
| rolling-03 | `d916b3fd-e110-5a11-af32-41fdef07aaa0` | 56,026941 → 56,051196 | +0,043292% |

To względne zmiany MSE, nie procent błędnych prognoz ani koszt biznesowy.
Nie wykazano statystycznej nieistotności tych różnic. Właściciel zaakceptował
je jako wyjątki dla tego konkretnego rezultatu developerskiego.

Pełne [oryginalne metryki](evidence/04-v12-final/metrics.json) i
[wynik wykonania](evidence/04-v12-final/completion.json) pozostają dostępne.
[Protokół jakości 2.0](forecast-quality-v2.md), progi, podział danych,
receptury i historyczne wyniki są niezmienione. Walidacja służyła do kalibracji;
holdouty v12 zostały wykorzystane i nie kwalifikują poprawionego modelu.
Portfolio final test nie został otwarty.

## Odtwarzanie i trwały eksport

- Kampania: `functional-v12-campaign-sha256-f845efee1a294f1bc3a4cb1c8541bac670903f80ed54a63cd94d3b3384c2cefb`.
- Freeze: `functional-v12-freeze-sha256-c2c375e5bdcb55454a5aaf3775ed60dd2647f0ebbdeac93b264be3b8d92c5c95`.
- [Niezależny replay](evidence/04-v12-final/replay_receipt.json) zapisanych
  parametrów, predykcji i metryk przeszedł bez ponownego fitowania.
- Run: `functional-v12-run-sha256-345a725d435a477374292cb9483350fb5c50c8ba87d06668c727e0a9f964fb6b`.
  [Manifest](evidence/04-v12-final/run_manifest.json) zachowuje **663 pliki /
  31 994 594 655 B**, checkpointy wszystkich 64 kohort, kampanię, receptury,
  predykcje, replay, kartę modelu, signature i przykład wejścia.
- SHA-256 manifestu runu:
  `29bf6837ca2cb7504213168743239b037041f375763bc8f01ae28c1f6d09b26e`.
- Trwały katalog: `data/generated/ai04-v12-runs/<run_id>` w głównym workspace.
- [Weryfikacja rzeczywistego runu z odłączonego wheel](evidence/04-v12-final/detached-verification.json)
  przeszła poza checkoutem, z `python -I`, bez nowych fitów i nowej kwalifikacji.
  Pakiet pochodził z zamrożonej implementacji `b480a55`.

Kontrola decyzji odbioru: `make forecast-acceptance-check`.
Kontrola eksportu wymaga zachowanego wheel właściwej implementacji; polecenie
`scripts/run_forecast_functional_v12_campaign.py verify --run <run_dir>`
sprawdza zachowane dane, bez źródłowego generatora i treningu.
Nie należy uruchamiać `score` ponownie w celu zmiany historycznej oceny.

## Handoff do AI 05

AI 05 otrzymuje ten konkretny run oraz osobną decyzję akceptacyjną.
Adapter musi zachować oddzielną medianę, średnią, przedziały, wszystkie kohorty,
lineage, ID, oryginalne czasy i sumy kontrolne. Stary importer jednej prognozy
nie jest zgodny z tym formatem. Czas importu zapisuje się osobno; nieznanych
czasów treningu nie wyprowadza się z czasu eksportu.

Gotowość etapu AI 04 nie zmienia zapisanej kwalifikacji modelu i nie nadaje
automatycznej promocji MLflow/registry, batch ani serving. Te decyzje należą
do odbioru AI 05. Akceptacja nie rozciąga się na inne runy lub nowe odstępstwa.

## Zakończenie dalszych prób

[V13](forecast-functional-v13.md) pozostaje zachowanym eksperymentem rozwojowym.
Przygotowanie zatrzymano po wyborze v12, przed oceną holdoutów prognozy v13.
W repozytorium usunięto aktywację generacji i jej automatyczny trigger po pushu.
Nowa kampania wymaga nowej decyzji, planu i jawnego uruchomienia.
Wcześniejsze źródła, wyniki, rejestr użycia testów oraz freeze są zachowane.
