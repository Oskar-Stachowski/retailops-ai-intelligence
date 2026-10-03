# Cechy PIT i czasowy podział AI 08

Ten przyrost dodaje cechy dla `product_id × stock_location_id × as_of`
oraz członkostwo train/tune/calibration/test. Korzysta z publicznych faktów
curated 1.1 i osobnych [etykiet](stockout-labels.md). Historyczny forecast
upstream, dopasowanie LR/HGB, kalibracja i ocena modelu pozostają kolejnymi
krokami. Wyniki jawnie mają `upstream_forecast_ready=false`, `model_ready=false`.

## Wiedza w origin

Każdy wiersz źródła musi mieć `curated_available_at <= as_of`. Najpierw
odcinana jest późniejsza wiedza, dopiero potem wybierana wersja i liczona
cecha. Fakty z późniejszym czasem wystąpienia także są wykluczane. Znany
plan przyszłej dostawy jest dozwolony; jej przyszłe faktyczne wykonanie nie.

| Cechy | Wejście i reguła dostępności |
|---|---|
| Dostępny zapas i wiek | Ostatni znany snapshot, uzupełniony tylko o znane ruchy nieuwzględnione przy jego cutoff; najwyżej 24 godziny |
| Średnia i zmienność sprzedaży | Ostatnia znana wersja dobowej obserwacji; suma wszystkich aktywnych kanałów wspólnego magazynu |
| Pokrycie sprzedaży | 28 dat UTC kończących się datą origin; brak jednego wymaganego kanału daje brak danych całego dnia |
| Historia ograniczeń zapasu | Ledger i znane świadectwo kompletności od opening stock przez pełny dzień; nawet chwilowe zero oznacza dzień ograniczony |
| Historyczne nowe braki | Liczba przejść dodatniego zapasu do zera w 27 zakończonych dniach tej historii; `null`, jeśli choć jeden nie ma pełnego pokrycia |
| Days of supply | Znany zapas / historyczna średnia sprzedaży; brak danych albo zerowy mianownik daje `null` |
| Otwarte zamówienia, dostawy do 7 dni, zaległe dostawy | Znane zamówienia minus znane wykonane części; ostatnia wersja planu znana w origin |
| Następna planowana dostawa i lead time | Znana planowana data oraz aktywna oferta dostawcy o najwyższym priorytecie |

Dzień origin nie jest automatycznie uznawany za zakończony: sprzedaż
opublikowana dopiero o następnym północnym cutoff nie jest jeszcze znana.
Świadectwo historii musi pokrywać cały dzień; niepełna informacja o zapasie
nie staje się informacją, że towar był dostępny cały dzień.

Sprzedaż to obserwowane sztuki, bez estymacji latent demand. Zapisujemy
dwa warianty średniej i days of supply: ze wszystkich znanych obserwacji
oraz tylko z dni o potwierdzonym dodatnim zapasie przez cały dzień.
To wejście do późniejszego porównania wpływu cenzorowania; nie jest jeszcze
wynikiem ablation modeli. Liczniki rozróżniają dni ograniczone zapasem,
dni bez weryfikacji zapasu i dni bez kompletnej sprzedaży.

Przy pozytywnym znanym zapasie wymagane jest co najmniej 7 kompletnych
dni sprzedaży. Nieznany/stary zapas, nieaktywność produktu albo nieznane
mapowanie asortymentu oznaczają `insufficient_data`. Znane zero przy
aktywnym produkcie i mapowaniu daje `already_stockout`. Nie powstaje
pozorne niskie prawdopodobieństwo ryzyka. Zapas nadal ma semantykę
`available_qty=on_hand`, `reserved_qty=0` z kontraktu inventory 1.1.

## Pochodzenie i odtworzenie

Builder ponownie weryfikuje curated i inventory reconciliation, czyta
wyłącznie 11 jawnych tabel faktów/planów i porównuje ich digest po odczycie.
Nie czyta tabel przyszłych stanów, kwalifikowanych wyników ani latent demand
do obliczania cech. Qualification ID pozostaje przypiętym identyfikatorem
pochodzenia; sam nie jest cechą.

Każdy punkt zachowuje liczbę i hash znanych rekordów dla każdej tabeli oraz
jej maksymalny czas dostępności. `feature_available_at` jest maksimum tych
czasów i nie przekracza origin. Dataset ID wiąże source, public snapshot,
qualification i curated IDs, politykę, kod konsumenta, lock, Python i pełne
punkty. Kontekst lineage obejmuje także znane rekordy służące ustaleniu
kompletności lub braku planu. `features-verify` odtwarza cały wynik;
ponowne zahashowanie zmienionych wartości nie wystarcza.

Zapis jest prywatny (`0600`), atomowy i nie nadpisuje innej treści. Ten sam
wynik daje `reused`. Wejście ma limit 64 MiB / 500 000 wierszy, punkty cech
10 000, a wynik cech 16 MiB. Większy limit dotyczy wyłącznie tego odczytu;
domyślny limit metadanych pozostał 4 MiB, z kontrolą duplikatów kluczy,
liczb niebędących skończonymi i struktury JSON. Są to limity ograniczonego
przyrostu, a nie deklaracja wydajności pełnego profilu treningowego.

## Cztery okresy w czasie

`SplitPolicy` zawiera jawne, rosnące daty UTC. Przedziały są lewostronnie
domknięte i prawostronnie otwarte. Zbiór cech i etykiet musi pochodzić
z tego samego source i qualification; publiczny i prywatny snapshot mogą
mieć różne snapshot IDs. Duplikat fizycznego origin zatrzymuje budowę.

Do train, tune i calibration wchodzą tylko punkty z kwalifikującymi się
cechami i dojrzałą etykietą, której **koniec siedmiodniowego okna i czas
dostępności są ściśle wcześniejsze od początku następnego okresu**.
Równość granicy także wyklucza wiersz. Pozwala to usuwać zarówno okna
przecinające granicę, jak i późno dostarczone wyniki już zakończonych okien.
Wiersz niekwalifikujący się zachowuje powód wyłączenia; nie staje się klasą 0.

Test otrzymuje wyłącznie członkostwo po sprawdzeniu dojrzałości i dostępności
do `evaluated_at`. Raport nie liczy klas ani metryk testu. Helper
`development_labels` dopuszcza wyłącznie train/tune/calibration i ponownie
odtwarza cały split, więc ręczne przeniesienie wiersza testowego do train
jest odrzucane. Adapter waliduje istniejący prywatny zbiór etykiet, również
schemat rekordów testu; nie oznacza to oceny ich jakości. Późniejsza kampania
modelu nadal musi mieć osobną kontrolę i rejestr dostępu do final test.

`temporal_membership_ready=true` oznacza niepusty każdy okres i obie klasy
w każdym z trzech okresów development. Nie oznacza gotowego modelu,
wystarczającej liczebności do kalibracji ani zaliczonego final test.
Nie fitujemy imputacji, skalowania, class weights, kalibratora lub progów.

## Polecenia offline

```sh
python -m retailops_ai.stockout.prepare features-build \
  --curated /path/to/curated \
  --output /path/to/private-output/features.json

python -m retailops_ai.stockout.prepare features-verify \
  --curated /path/to/curated \
  --output /path/to/private-output/features.json

python -m retailops_ai.stockout.prepare split-build \
  --curated /path/to/curated \
  --features /path/to/private-output/features.json \
  --labels /path/to/private-output/labels.json \
  --source /path/to/private-inventory-snapshot \
  --policy /path/to/split-policy.json \
  --output /path/to/private-output/split.json \
  --allow-evaluation-truth
```

`split-verify` używa tych samych parametrów i weryfikuje istniejący wynik.
Przed split adapter odtwarza cechy z curated i etykiety z prywatnego source.
Samo podanie pliku cech/etykiet albo ID nie stanowi ich weryfikacji.
Nie uruchamia usług ani nie zapisuje registry, batch lub API.
