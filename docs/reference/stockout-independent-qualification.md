# Niezależna ocena development i karta partycji

Pakiet `stockout_qualification` ma osobną tożsamość. Nie zmienia wcześniejszego
`stockout_training` ani jego artefaktów. Publiczny builder ponownie odtwarza
całe cechy/etykiety/upstream/temporal, wiąże prawdziwe bundle IDs i sprawdza
seals wszystkich rodziców przed zwróceniem wyniku. Wymaga jawnego opt-in do
prywatnej weryfikacji etykiet. Nie otwiera final testu i nie zapisuje registry.

## Chronologia nowej receptury

[Polityka](stockout-independent-development-policy.json) jest zapisana przed
niezależną oceną nowego profilu. Z wcześniejszego development wybiera:

1. Base train: origin oraz pełne `window_end_at` i `label_available_at` są
   ściśle wcześniejsze niż 20 maja. Preprocessing i oba modele fitują tylko
   te wiersze, przy naturalnej częstości klas.
2. Calibration fit: późniejsze originy od 20 maja, których okna i etykiety
   są znane ściśle przed 5 czerwca. Model bazowy nie widział ich outcomes.
   Sigmoid fituje wyłącznie te późniejsze out-of-sample log odds. Wiersze
   przecinające granicę base fit są purged, a nie przenoszone do innej roli.
3. Tune: oryginalna dojrzała rola tune od 5 czerwca wybiera rodzinę/wariant
   na raw AP/Brier. Wybór jest zamrożony z cutoff 24 czerwca. Porównanie
   obejmuje sześć identycznie ocenianych LR/HGB wariantów; raw-sales-only
   pozostaje kontrolą ablation, bez udziału w wyborze champion.
4. Independent development validation: oryginalna rola calibration od
   24 czerwca ocenia model oraz sigmoid. Jej cechy i cele nigdy nie fitują
   preprocessingu, parametrów, kalibratora ani wyboru rodziny. Dodatkowy
   podział nie zmienia wcześniejszego splitu ani final test membership.

Nowy selection cutoff także ogranicza późniejsze serving; wcześniejszy
cutoff samych parametrów modelu nie oznacza, że jego wybór był już znany.
Dokładne klucze i cele każdej roli mają osobne skróty; role są rozłączne.
Pełne metadane availability muszą zgadzać się ze zweryfikowanym temporal
rodzicem. Wynik po ponownym zahashowaniu nie zastępuje replay.

## Propozycja jakości przed final testem

Wymagane są całość, osiem kategorii, dwie fizyczne lokalizacje oraz segment
z historycznym ograniczeniem zapasu. Każdy wymagany segment ma co najmniej
20 wierszy i po 5 przypadków obu klas. Brak segmentu lub klasy daje
`not_evaluable`; brak pełnego oczekiwanego universum blokuje kwalifikację.

Każdy wymagany segment musi mieć AP powyżej jego częstości zdarzeń,
Brier niższy niż stałe prawdopodobieństwo wyznaczone na base train oraz
ECE najwyżej 0,15. ECE to ważona liczebnością bezwzględna różnica między
średnią predykcją i obserwowaną częstością w pięciu zamrożonych binach.
Sigmoid wybranego modelu musi zachowywać dodatnie nachylenie. Obliczalność
metryki i bramka jakości są osobnymi wynikami.

To wersjonowana **propozycja** kryteriów przed końcową kampanią, nie zgoda
na progi operacyjne, promocję ani deklaracja jakości final testu.
Raport pokazuje też unconstrained coverage/metryki. Jednoklasowy slice nie
otrzymuje fikcyjnego AP; operacyjny ranking inventory-constrained ma własną
wymaganą bramkę. Nakładające się siedmiodniowe okna nie są niezależnymi
epizodami, a liczebność wierszy nie stanowi gwarancji dokładności populacyjnej.
Syntetyczny profil pośredni nie otrzymuje nazwy pełnego `ai-training`.

## Karta i publikacja offline

Karta 2.0 wskazuje rzeczywiste feature/label/upstream/temporal bundle IDs
oraz qualification ID. Nie tworzy mostka do v1 JSON IDs. Pokazuje trzy
tabele współczynników LR, znaczenie cech wybranego wariantu na tune,
factual context, niezależne metryki, liczebności oraz ograniczenia.
Factual context pochodzi z pełnego replay; zerowy, nieznany lub przestarzały
zapas jest odrzucany. Wyjaśnienia nie przypisują cechom przyczynowości.

Capsule ma limit 16 MiB, karta 8 MiB. Zapis jest atomowy, prywatny 0600,
immutable i powtarzalny. `verify` odtwarza cały capsule, nie tylko hash.
Build może zapisać uczciwy wynik `not_ready`; exit 0 oznacza poprawny odbiór
artefaktu, a nie zaliczenie jakości. Final test, threshold policy, registry,
promotion i całe AI 08 pozostają osobnymi kontrolami.

```sh
python -m retailops_ai.stockout_qualification.cli build \
  --curated /path/to/curated --private /path/to/private/snapshot \
  --features /path/to/features --upstream /path/to/upstream \
  --labels /path/to/labels --temporal /path/to/temporal \
  --policy docs/reference/stockout-independent-development-policy.json \
  --output /path/to/private-output/qualification.json --allow-evaluation-truth
```

`verify` ma te same argumenty. Output nie udostępnia wektora celów ani
metryk final testu. Wprowadzenie capsule nie zmienia AI 05 ani finalnego v12.
