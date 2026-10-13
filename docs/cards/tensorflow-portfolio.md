# Karta modelu — TensorFlow w portfolio AI 09

Właściciel: RetailOps AI Intelligence. Zakres:
[TensorFlow i konsolidacja portfolio](../ai09-portfolio-ready.md).
**Wynik końcowy: TF odrzucony do nowej promocji. Odbiór publikacji etapu w toku.**
Ta karta dotyczy nowego modelu z rzeczywistych danych Source. Historyczne
modele i wyniki [development](tensorflow-development.md) pozostają odrębne.

## Przeznaczenie i wynik

Model eksperymentalny przewiduje obserwowaną sprzedaż w sztukach dla
produktu, miejsca sprzedaży, kanału i originu, osobno na horyzontach 1–14.
Zwraca osobne średnią i medianę, bez zaokrąglania do całych sztuk.
Nie przewiduje ukrytego popytu, strat przez braki zapasu ani prawdopodobieństwa
stockout. Origin zamyka dzień UTC; późniejsze fakty nie są wejściem modelu.

TF jest jednym ustalonym challengerem. Stały punkt odniesienia to history28;
history7, weekday28, RF mean i HGB mean/median dostarczają dodatkowych porównań.
Wszystkie rodziny otrzymują tę samą populację ocenianych kluczy i te same
reguły dostępności cech. Odrzucenie TF jest poprawnym zakończeniem etapu.

## Tożsamość i odtworzenie

- Model: `model-sha256-d9747b63efb9136fa967951e0cf38ceb186d0922148bb21baf614ea73fcb8d73`.
- MLflow run: `8431adeb9d024647a1d19ea01a4a2594`, zachowany w oryginalnym
  file store porównania; wspólnego Registry nie zmieniono.
- Porównanie: `ai09-development-sha256-b4fd3a05b47ff21d661da87152d7bb761ba979555e7396748dd825f9c317db7a`.
- Cechy: `features-sha256-0e90402283bb4247686dbde30baf20c89f7b6117fd5643cb7c991e27c1a9f9a1`.
- Split: `split-sha256-eabe4ce425a13d8a4d85f5706476dfc609c3fcbe9ea49fcdcaf214bdca6c899a`.
- Zamrożenie modelu, kalibracji i oceny: SHA-256
  `4255cf3a06436516675053a47f99cde8fb3444d8aae54ab00858a1b438fde8a2`.

[Oryginalny odbiór treningu](../evidence/09-132-portfolio-development-repair-complete.json)
wiąże Actions run, kompletny ZIP, model IDs i koszty.
[Odbiór kalibracji](../evidence/09-134-portfolio-calibration-complete.json)
wiąże archiwum modeli z 73 zweryfikowanymi plikami i osobny kalibrator.
Format MLflow Keras obejmuje `.keras`, podpis, preprocessing, recepturę
i lock zależności. Podpis: `float32[-1,1428] → float32[-1,14,2]`, kolejność
wyjść mean/median. Odtworzenie w świeżym procesie CPU przeszło z tolerancją
`rtol=atol=1e-6`; rzeczywisty test adapterów potwierdził zgodność predykcji
z oryginalnym wykonawcą wszystkich rodzin, bez dodatkowego treningu.

## Architektura, dane i chronologia

Historia 28 dni, maski braków i aktywności oraz cechy targetów znane w origin.
Train-only imputacja, one-hot vocabulary, normalizacja i skala targetu;
nieznane kategorie mają osobny fallback. Sieć Dense 32 → 16 → 28,
ReLU w warstwach ukrytych, softplus wyjścia i reshape 14 × 2.
Adam, learning rate 0,001, batch 32, masked MSE średniej + MAE mediany,
initialization seed 42. Maksimum 25 epok, patience 4; zakończono 11 epok
i odtworzono najlepszą epokę 7. Jedna konfiguracja, bez wyboru na final.

[Receptura danych](../reference/ai09-portfolio-development-v1.json) używa
oryginalnego Source `d2363a5c78e0b190132e5e3d804da73b3136487d`, profilu
eksperymentu `ai09-portfolio-development-1` opartego na `ai-load`:
365 dni, 8 produktów, 3 pary sprzedaży, 2 lokalizacje zapasu, data seed 42.
8760 wierszy dziennego panelu nie jest liczbą wszystkich zdarzeń źródła.

| Rola | Originy | Kwalifikowane etykiety / wszystkie |
| --- | --- | ---: |
| Trening | 2026-07-01–2026-09-28 | 27168 / 30240 |
| Early stopping | 2026-10-15–2026-11-11 | 8400 / 9408 |
| Osobna kalibracja | 2026-11-28–2026-12-11 | 4223 / 4701 |

Role oddziela 15 dni purge; cutoff etykiet respektuje ich dojrzałość.
Walidacja użyta do early stopping jest diagnostyką, nie niezależnym dowodem
jakości. Przedziały 90% dopasowano osobno dla modelu i horyzontu na roli
kalibracji, przy 279–304 kwalifikowanych etykietach na horyzont. Oryginalny
artefakt treningowy nadal uczciwie opisuje brak ówczesnych przedziałów;
późniejszy kalibrator jest oddzielnym, przypiętym artefaktem.

Final obejmuje trzy seedy danych 42/137/2026, stałe modele i kalibratory,
originy 2027-01-17–2027-03-15, cutoff 2027-03-30 i wagi po 1/3.
Daty oznaczają symulację, nie zebrane przyszłe obserwacje. Zasady opisuje
[zamrożony protokół](../reference/ai09-portfolio-assessment-v1.json).
Pierwsze trzy próby zakończył błąd przygotowania danych przed inferencją.
[Naprawa](../reference/ai09-portfolio-final-repair-v1.json) korzysta z tych
samych archiwów i ujawnia ponowny dostęp; nie resetuje historii final.
Wszystkie trzy naprawione przebiegi zakończyły przygotowanie, inferencję
i scoring. Ich metryki odtworzono dokładnie z zapisanych predykcji i etykiet.

## Końcowy wynik i decyzja

[Raport portfolio](../ai09-forecast-portfolio-report.md) zawiera wszystkie
metody, seedy, segmenty, liczności i przyczyny odrzucenia. Każdy seed ma
19443 wspólne klucze i 17437 kwalifikowanych etykiet. Równa średnia trzech
seedów daje TF: MAE średniej 2,8118 szt., MSE średniej 27,2648 szt.²,
MAE mediany 2,8530 szt., znormalizowany bias średniej −31,58% i coverage
przedziału 89,12%. Stały history28 ma odpowiednio MAE średniej 2,8210,
MSE 25,0327 i MAE mediany 2,8329.

Każdy seed TF narusza limit biasu 10%. Segmenty historycznego wolumenu
`zero` i `high` nie mają próbek i pozostają `not_ready`. Wynik zamrożonej
reguły to `reject_for_new_promotion_retain_existing_portfolio`.
Nie przeprowadzono dodatkowego strojenia ani wyboru innej rodziny na final.
Raportowane rozrzuty trzech seedów nie są przedziałami ufności.

## Zmierzone koszty i ograniczenia

| Pomiar TF na Linux x86_64 CPU | Wynik |
| --- | ---: |
| Przygotowanie wejść TF | 43,93 s |
| Sam trening | 13,47 s |
| Cały worker treningowy | 18,00 s |
| Szczyt RSS workera | 720691200 B |
| Odtworzenie w świeżym procesie z importem frameworka | 2,97 s |
| Predykcja 9408 kluczy validation po odtworzeniu | 0,069 s |
| Model z preprocessingiem i zależnościami | 334935 B |

Te pomiary nie obejmują całego Source ani pobierania archiwów. Cała naprawa
development z transportem trwała 582,30 s, z próbkowanym szczytem drzewa
około 2,75 GiB. Koszt pierwszej nieudanej próby pozostaje osobno zachowany.
Python 3.11.15, TensorFlow 2.20.0, Keras 3.11.3 i MLflow 3.4.0 są przypięte
w locku. Włączono determinism dla tej platformy i wersji; nie deklarujemy
bitowej identyczności między systemami.

Dane są syntetyczne i mają małą szerokość panelu. Wyniki nie potwierdzają
uogólnienia na rzeczywistego detalistę, pełnego `ai-training`, innych
produktów ani większej skali. Nieznana część utraconej starszej historii
dostępu nie jest traktowana jako dowiedzione zero. Nowe źródła wygenerowano
prospektywnie po zamrożeniu modeli i kalibracji, poza znanym wcześniejszym
okresem danych. Rozrzut trzech seedów jest opisowy, bez deklarowania
przedziału ufności dla różnic metryk.

Brak wymaganej próbki i nieokreślony WAPE pozostają jawne. TF musi spełnić
zamrożone wymagania globalnie i w wymaganych segmentach każdego seeda,
aby uzyskać pozytywną rekomendację do dalszego rozważenia. Słaby wynik
nie uruchamia kolejnego strojenia. Ta kampania nie przełącza championa,
nie publikuje prognoz użytkowych i nie udziela zgody na produkcję.
