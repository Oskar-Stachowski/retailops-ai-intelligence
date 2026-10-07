# Karta i diagnostyka modelu development

Karta wiąże [odebrany model](stockout-training.md) z jego źródłami,
konfiguracją, wynikami, ograniczeniami i wyjaśnieniami. Dotyczy syntetycznego
development. Nie zalicza bramki jakości pełnego profilu i nie dopuszcza
modelu do produkcji.

## Odtwarzanie i tożsamość

CLI ponownie weryfikuje cechy, upstream, comparison, prywatne etykiety,
split i PIT kategorii. Odtwarza całe dopasowanie rodzica `development.json`
i porównuje jego treść, nie tylko checksum. Ponownie zahashowane zmiany
wag lub wyników nie zastępują replay. Zgoda `--allow-evaluation-truth`
dotyczy weryfikacji prywatnego źródła, bez oceny końcowego testu.

Pakiet `stockout_explanation` ma osobną tożsamość kodu. Dodanie karty nie
zmienia ID danych, modeli ani wyboru provisional. Manifest karty przypina
rodziców, model ID, recepturę, kod i hash całej treści. Zapis ma limit
8 MiB, tryb 0600, jest atomowy i nie nadpisuje innej treści.
`verify` odtwarza również całą kartę; zmiana treści i jej hasha jest odrzucana.

## Co wyjaśnia karta

Trzy tabele LR pokazują wszystkie współczynniki, intercept i train-only
centrowanie/skale. Wagi dotyczą **surowych log odds po preprocessingu,
przed sigmoid**. Waga cechy standaryzowanej nie jest zmianą
prawdopodobieństwa przy zwiększeniu surowej wartości o jeden. Kategorie
mają pełny regularizowany one-hot z nieznaną wartością jako wektorem zer;
poszczególna waga nie oznacza niezależnego wpływu kategorii.

Wybrany provisional model otrzymuje permutation importance tylko na tune,
przy surowych prawdopodobieństwach. Każda grupa jest przetasowana pięć razy,
seed 42, bez ponownego treningu lub wyboru modelu. Wartość numeryczna i jej
wskaźnik braku przemieszczają się razem; cała kategoria przemieszcza się
jako jeden poprawny blok one-hot. Pięć permutacji jest wspólnych dla grup.
Raport podaje spadek AP i wzrost Brier, każdy powtórzony wynik, średnią
i odchylenie. Ujemne znaczenie pozostaje ujemne, bez clippingu.

To zależność wyniku od wejścia w tej konkretnej próbie, nie przyczyna
braku towaru. Skorelowane cechy mogą dzielić lub ukrywać znaczenie.
Przetasowanie może tworzyć fizycznie niespójne kombinacje; takie macierze
nie są dopuszczane do serving. Odchylenie opisuje losowość przetasowania,
nie przedział ufności z populacji. Tune było użyte do wyboru modelu,
więc znaczenie cech nie jest nową niezależną oceną jakości.
Kalibrator dopasowany później nie jest stosowany w historycznym tune.

Lokalne factual reason codes opisują potwierdzony kontekst każdego punktu
tune: znany zapas i wiek snapshotu, days of supply z obserwowanej sprzedaży
oraz ze zweryfikowanych dni in-stock, znane plany i zaległe zamówienia,
historyczne ograniczenia lub braki wiedzy o zapasie. Brak danych nie staje
się zerem. Są to fakty PIT, **bez lokalnej atrybucji wyniku HGB** i bez
stwierdzenia „model wyliczył ryzyko z powodu X”. Helper odrzuca
`already_stockout`, `insufficient_data` oraz snapshot starszy niż 24 h.
Nie nadaje probability, pasma ryzyka ani nie zatwierdza progów.

Karta zawiera klasy wyłącznie train/tune/calibration, liczniki segmentów,
coverage, wybrany model i wcześniejsze metryki. Final test ma jedynie
członkostwo. Kalibracja in-sample, nakładające się okna 7 dni, małe segmenty,
syntetyczność i niezatwierdzone koszty/capacity pozostają jawne.

## Użycie

Przygotuj prywatny katalog output. `verify` ma identyczne argumenty:

```sh
python -m retailops_ai.stockout_explanation.cli build \
  --curated /path/to/curated \
  --features /path/to/features.json \
  --upstream /path/to/upstream.json \
  --comparison /path/to/comparison.json \
  --labels /path/to/labels.json \
  --split /path/to/split.json \
  --source /path/to/private-inventory-snapshot \
  --development /path/to/development.json \
  --output /path/to/private-output/model-card.json \
  --allow-evaluation-truth
```

[Odbiór](../evidence/08-05-stockout-card.md) podaje rzeczywiste wyniki.
[Plan zasobów](stockout-profile-resources.md) określa kolejną zmianę
potrzebną przed większą generacją. Niezależna ocena, threshold policy,
lifecycle oraz batch/read API pozostają otwarte; cały AI 08 nie jest ready.
