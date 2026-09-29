# Forecasting 04.2 — aktywny panel i cechy

Builder używa [zadania i originów 04.1](forecasting.md) oraz zweryfikowanego
[curated 03](curated.md). Wytwarza lokalne, typed Parquet inputs. Nie trenuje
modelu, nie dopasowuje encoderów/imputacji ani nie tworzy finalnego splitu.

## Kalendarz i kompletność panelu

Dla każdego origin zamykającego D sprawdzamy dni **D−27…D**. Wiersz powstaje
tylko dla produktu w lifecycle, znanego assignmentu selling location/channel
i znanego aktywnego asortymentu. Okresy są półotwarte. Nie tworzymy iloczynu
wszystkich produktów, sklepów, magazynów i kanałów. Raport porównuje potencjalny
iloczyn znanych produktów/par z rzeczywistymi aktywnymi punktami i targetami.

Historia wybiera najwyższą wersję znaną w origin z `daily_demand_versions`.
Nie korzysta z final observations, transakcyjnej ceny ważonej sprzedażą,
wyników operacyjnego forecastingu, inventory, dostaw ani simulation truth.
Panel zachowuje cztery różne stany:

| Stan | Ilość | Kompletność |
|---|---|---|
| `observed_positive` | dodatnia, znana | true |
| `observed_zero` | potwierdzone 0 | true |
| `closed` | potwierdzone 0 | true, zachowane zamknięcie |
| `missing` | null | false; także obserwacja jeszcze niedostępna |

Brak dnia nie przesuwa dat kolejnych obserwacji. Dzień poza lifecycle,
assignmentem lub asortymentem nie staje się sztucznym zerem ani brakującą
aktywną obserwacją. `is_active_assortment=true` jest jawne w każdym wierszu.
Closed zero pozostaje znaną historią dla lagów/statystyk; zachowujemy osobny
status i count. Target z known closed calendar pozostaje w tabeli z
`target_calendar_eligible=false`. Nieznany kalendarz także nie daje eligibility.

## Historyczne cechy wspólne dla wszystkich horyzontów

**`origin_lag_k = sales[D+1−k]`**, dla k=1/7/14/28. To dni kalendarzowe,
a nie poprzednie dostępne wiersze. Wszystkie horyzonty 1–14 używają tych
samych historycznych wartości i tego samego `history_context_sha256`.

Rolling mean/std/count używają okien **D+1−w…D**, w=7/14/28, z dostępnością
do origin. Średnia i population std (`ddof=0`) wykorzystują wyłącznie znane
obserwacje, w tym potwierdzone zera i closed zero. Missing nie wchodzi jako zero.
Count pokazuje rzeczywistą liczbę znanych obserwacji; pusta próba daje count=0,
mean/std=null, a pojedyncza znana obserwacja daje std=0.

W źródle odebranym w 03 sprzedaż D jest zwykle dostępna dopiero o północy
D+1. Przy cutoffie 23:59:59 D **lag 1 pozostaje null**, a rolling count może
wynosić w−1. Nie przesuwamy granicy dostępności ani nie zastępujemy lag 1
wartością D−1. Dostępność zachowuje mikrosekundy, także na granicy cutoff.

Draft policy wskazuje 28 aktywnych dni historii i minimum 7 znanych obserwacji.
`insufficient_history` oznacza niespełnienie któregokolwiek warunku. Wiersz
pozostaje w coverage i nie dostaje zera/fallback prediction. To oddzielne
pole od eligibility kalendarza. Kwalifikacja historii, świeżości, etykiet
i polityka cold start są w [formalnym kontrakcie 04.3](forecast-manifests.md).
Baseline fallback wymaga osobnego odbioru; domyślnie pozostaje insufficient_data.

## Kalendarz, kategorie i plany

Typed allowlist zawiera **36 cech**. Pełną kolejność/typy podaje
[FEATURE_TYPES](../src/retailops_ai/forecasting/features_contract.py), a
[schema wiersza](../contracts/forecast/v1/input_row.schema.json) wraz z walidacją
semantyczną blokuje dodatkowe cechy, błędne typy i przyszłą dostępność.

| Grupa | Pochodzenie / polityka |
|---|---|
| Lagi i rolling | Przypięty history context; jeden na serię/origin |
| Weekday, ISO week, month, quarter, weekend | Deterministycznie z target date w UTC; weekday 0–6 |
| Public holiday, Easter, Christmas, Black Friday, Cyber Monday, opening | Known `business_calendar`, bez rekonstrukcji reguł generatora |
| Sezon kategorii | Known `category_calendar` dla target date/category |
| Category ID, brand | Dostępny `product_catalog`; bez niewersjonowanych nazw/taxonomii |
| Country/jurisdiction | Dostępny business calendar; statyczne selling location bez availability nie jest historycznym źródłem cech |
| Channel | Selling grain, zapisany w Parquet jeden raz |
| Regular price/currency | Najwyższy znany scope: location_channel → location → channel → global |
| Promotion offer/type/discount/minimum quantity | Najwyższa priority aktywnej znanej kampanii; najnowsza cancellation nie przywraca starszej wersji |

Plan obowiązuje w target date i musi być znany w origin. Nieznany override
nie zastępuje znanej ceny globalnej. Brak planu daje missing; nie sięgamy po
actual price. Niejednoznaczny znany scope/period/version lub promotion priority
blokuje build. Wszystkie rodzaje modeli dostaną ten sam input.

Promocja jest **ofertą**, nie twierdzeniem o przyszłym rabacie. Bundle zachowuje
minimum quantity; nie wykorzystujemy przyszłego koszyka do sprawdzenia progu.
Regular price nie jest zrealizowaną ceną po rabacie. Minor units zachowują
precyzję jako integer w JSON i Decimal128(38,0) w Parquet, bez FX/zaokrąglania.
Discount ma dokładne basis points. Kategorie pozostają jawne i niewyuczone.

Każda cecha ma kind, status, source availability, observed-through/effective
date i referencje, lub odwołanie do history context. Każdy znany punkt historii
wiąże quantity z raw source SHA i znanymi mapowaniami. Dostępność po origin
jest błędem. Mathematical calendar ma availability równą origin; count i
informacja o braku kampanii opisują stan wiedzy w tym origin.

## Użycie i artefakty

```bash
uv run --locked --extra snapshot retailops-ai-forecast inputs-build \
  --curated-dir data/generated/curated/<curated_dataset_id> \
  --calendar data/generated/forecast-calendars/<calendar_id>.json
uv run --locked --extra snapshot retailops-ai-forecast inputs-verify \
  --inputs-dir data/generated/forecast-inputs/<inputs_id>
make forecast-features-check
```

Output ma `inputs_manifest.json`, przypięty calendar manifest oraz dwie tabele:
`history/` z contexts i `features/` z native typed kolumnami oraz pełnym body JSON.
Context zawiera tylko aktywne punkty kalendarza, włącznie z missing. Body JSON
zachowuje dostępność, przyczyny braków, referencje i coverage; typed kolumny
można odczytać standardowym PyArrow. Weryfikacja sprawdza ich zgodność.

`forecast-inputs-sha256-…` identyfikuje **draft inputs**, nie finalny feature set.
Wiąże parent IDs, calendar ID, kod/lock/runtime, politykę, typy, rzeczywistą
treść i coverage. Ten draft format nie nadaje `feature_set_id` ani `split_id`;
[manifest 04.3](forecast-manifests.md) wiąże go z formalnym feature setem i splitem.
Publikacja
jest atomowa bez nadpisania; rerun zachowuje content ID i oryginalne bajty.
Generated time i ścieżki nie definiują ID. Zmiana source parent zmienia ID,
choć późniejsze fakty nie zmieniają wartości dawnych fixed-origin inputs.

Weryfikacja sprawdza hashes, schema, unikalny grain, granice czasu, referencje
do właściwego contextu oraz przelicza lagi/statystyki z tej historii. Samo
przeliczenie file hashes nie legalizuje zmienionej średniej. Nie zastępuje to
podpisu/review ani pełnego odbioru modelu. Reader buforuje zweryfikowane osiem
allowlisted tabel w dyskowym indeksie; nie wczytuje source od nowa dla każdego
horyzontu. Source zmieniony podczas indeksowania jest odrzucany.

Limity: 250 tys. input rows w origin, 10 tys. serii/origin, 10 mln output rows
na tabelę; 2 GiB encoded source index, 2 GiB encoded/physical output, 10 tys.
plików, 1 MiB na logical row. Bufor ma 256 rows/16 MiB, Arrow batch limit 64 MiB.
Smoke nie kwalifikuje pełnego ai-dev/ai-training pod względem zasobów.

[Odbiór 04.2](evidence/04-02-features.md).
[Formalny feature/split contract 04.3](forecast-manifests.md) wersjonuje ten
draft input i dopasowuje preprocessing wyłącznie na train.
[Evaluator i baseline'y 04.4](forecast-baselines.md) używają zamrożonej historii.
Kolejny zakres: **04.5 — RF i HistGradientBoosting**.
