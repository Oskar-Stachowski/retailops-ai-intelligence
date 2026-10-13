# AI 09 — końcowe porównanie prognoz i decyzja portfolio

**Decyzja: odrzucić TensorFlow do nowej promocji; zachować dotychczasowe portfolio.**
Trzy końcowe przebiegi wykonano i ich metryki odtworzono dokładnie z oryginalnych
predykcji i etykiet. Pełne kopie trzech archiwów sprawdzono pod względem rozmiaru i SHA-256;
pliki użyte do replay są zgodne z ich zawartością. Odbiór publikacji PR/main
pozostaje wymagany przed `ready` etapu. Negatywny wynik modelu nie otwiera
kolejnej rundy strojenia ani nie jest błędem wykonania kampanii.

Obowiązuje [zatwierdzony wariant 1](ai09-portfolio-ready.md). Nowa ocena
obejmuje forecasting. Wyniki anomaly i stockout są [historyczną konsolidacją](ai09-consolidated-portfolio.md),
bez ponownego treningu, świeżej wspólnej kampanii lub zmiany wcześniejszych
rozstrzygnięć. Warianty 2–3 pozostają propozycjami poza tym zadaniem.

## Dane i niezależność oceny

Oryginalny Source → kwalifikacja → snapshot → importer → curated → cechy,
profil `ai09-portfolio-final-1` oparty na `ai-load`: 365 dni, 8 produktów,
3 pary sprzedaży, 2 lokalizacje zapasu. Source i locki są przypięte w
[protokole](reference/ai09-portfolio-assessment-v1.json). To mały jawny profil,
nie pełny `ai-training`. Pełne source/snapshot/curated IDs, manifesty,
liczności, hashe predykcji i dowody replay wiąże [09.136](evidence/09-136-portfolio-final-complete.json).

Modele wytrenowano tylko na development. Walidacja early stopping i osobna
kalibracja poprzedzają final. Modele, preprocessing, 90% przedziały, segmenty,
metryki i reguła decyzji zostały zamrożone przed wygenerowaniem nowych źródeł.
Final: originy 2027-01-17–2027-03-15, cutoff etykiet 2027-03-30; daty są
symulowane. Znany wcześniejszy kompletny parent kończył się 2026-12-31.
Utracona, nieudokumentowana starsza historia pozostaje nieznana — nie
przedstawiamy jej jako dowiedzionego braku jakichkolwiek dawnych odczytów.

Pierwszą próbę każdego seeda zatrzymał błąd ścieżki przed inferencją.
[Historia niepowodzeń](evidence/09-135-portfolio-final-attempt-1.json) oraz
[jawna naprawa](reference/ai09-portfolio-final-repair-v1.json) zachowują te same
źródła i ujawniają ponowny dostęp. Nie było nowych generacji, fitów modeli,
kalibracji ani wyboru po zobaczeniu final. Natywny eksport ma wyłącznie role
`final_evaluation`; etykiety pozostają oddzielone od wejść inferencji.

| Seed | Klucze na każdą z sześciu metod | Kwalifikowane | Udział kwalifikowanych |
| --- | ---: | ---: | ---: |
| 42 | 19443 | 17437 | 89.68% |
| 137 | 19443 | 17437 | 89.68% |
| 2026 | 19443 | 17437 | 89.68% |

## Wyniki z wagą 1/3 dla każdego seeda

MAE i bias w sztukach opisują błąd liczby sprzedanych jednostek, MSE jest
w sztukach²; WAPE, znormalizowany bias i coverage podajemy jako udziały.
RF dostarcza wyłącznie średnią: brak mediany nie jest zerowym błędem.
Przedział RF jest centrowany wokół średniej; pozostałe wokół mediany.

| Metoda | MAE średniej | MSE średniej | WAPE średniej | MAE mediany | Znormalizowany bias średniej | Coverage przedziału |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| history7 | 2.8368 | 26.0586 | 71.09% | 2.8999 | -11.23% | 91.46% |
| history28 — stały reference | 2.8210 | 25.0327 | 70.68% | 2.8329 | -11.90% | 91.80% |
| weekday28 | 2.6891 | 25.1040 | 67.39% | 2.7218 | -2.07% | 91.15% |
| RF mean | 2.8256 | 26.4966 | 70.87% | — | -1.76% | 91.00% |
| HGB mean/median | 2.6636 | 24.4648 | 66.79% | 2.6177 | 4.61% | 90.88% |
| TensorFlow | 2.8118 | 27.2648 | 70.43% | 2.8530 | -31.58% | 89.12% |

To opis sześciu ustalonych metod, bez wyboru nowego championa na final.
Uśrednienie nie usuwa słabszego seeda. [Pełny agregat](evidence/09-final/summary.json)
podaje dla każdej metryki trzy wartości, równą średnią, minimum, maksimum
oraz populacyjne odchylenie standardowe. To opis rozrzutu trzech seedów,
nie przedział ufności ani dowód istotności statystycznej. Nie wykonano bootstrapu.

## TensorFlow wobec stałego history28

Delta jest różnicą TF minus reference na tych samych kluczach; dodatnia
wartość błędu oznacza gorszy wynik TF.

| Seed | MAE mediany TF | Delta MAE mediany | Delta MSE średniej | Bias średniej TF | Coverage TF | Status globalny |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 42 | 2.8707 | -0.1522 | -1.5431 | -28.92% | 89.73% | failed |
| 137 | 3.2170 | 0.1740 | 7.6552 | -42.74% | 86.97% | failed |
| 2026 | 2.4711 | 0.0383 | 0.5841 | -23.06% | 90.67% | failed |

Wszystkie seedy naruszają limit bezwzględnego znormalizowanego biasu średniej
10%. Pełne listy przyczyn zachowano w raportach
[42](evidence/09-final/seed-42.json), [137](evidence/09-final/seed-137.json)
i [2026](evidence/09-final/seed-2026.json). Nominalne coverage wynosi 90%,
a zamrożone minimum empiryczne 80%; wartości między 80% a 90% nie są
przepisywane na porażkę tej konkretnej bramki.

## Segmenty i brakujące próbki

Wymagane grupy zamrożono przed final: 14 horyzontów, 8 kategorii, 2 kanały
oraz 4 przedziały historycznego wolumenu. Tabela sumuje statusy TF w trzech
seedach; każdy status ma oryginalne liczności i przyczyny w raportach.

| Wymiar | passed | failed | not_ready |
| --- | ---: | ---: | ---: |
| category | 0 | 24 | 0 |
| channel | 0 | 6 | 0 |
| horizon | 0 | 42 | 0 |
| volume | 0 | 6 | 6 |

Segmenty `volume:zero` i `volume:high` nie mają próbek w żadnym seedzie.
Chodzi o historyczny wolumen, nie o wszystkie targety ze sprzedażą zero.
Te grupy pozostają `not_ready`, z nieokreślonymi metrykami; nie są sukcesem.
Nie wykazano odporności na nieobecne segmenty ani na pełną odłożoną macierz
zaprojektowanych scenariuszy demand/physical. Dane syntetyczne nie są dowodem
skuteczności u rzeczywistego detalisty. Historyczne wyjątki AI 04 nie są
stosowane do tej oceny TF.

## Rzeczywiste koszty i zachowana historia

Trening development: RF 10,71 s, HGB mean 3,97 s, HGB median 4,58 s,
cały worker TF 18,00 s (sam trening 13,47 s). TF ma model 334935 B
z preprocessingiem i zależnościami oraz odtworzenie w świeżym CPU 2,97 s.
Szczegóły: [karta modelu](cards/tensorflow-portfolio.md) i
[oryginalne zasoby](evidence/09-132-portfolio-development-repair-complete.json).
Kalibracja trwała 281,41 s z transportem, bez nowych fitów modeli.

| Seed | Pierwsza próba z przygotowaniem danych | Dokończenie z transportem | Przygotowanie final | Inferencja wszystkich metod | Scoring | Szczyt drzewa naprawy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 42 | 506.46 s | 919.05 s | 846.97 s | 52.52 s | 10.34 s | 1.272 GiB |
| 137 | 924.49 s | 436.07 s | 392.59 s | 31.38 s | 6.34 s | 1.275 GiB |
| 2026 | 577.80 s | 885.76 s | 816.98 s | 54.80 s | 11.02 s | 1.271 GiB |

Seedy wykonywano równolegle; sumy ich czasów nie są czasem kalendarzowym.
RSS drzewa jest próbkowany co 0,2 s, a CPU jest dolną granicą obserwowanego
czasu workerów. Limit 12 GiB oraz rezerwa hosta 1 GiB pozostały zachowane.
Czas środowiska Actions, instalacji zależności, transferu do komputera i CI
nie jest ukryty jako czas treningu; nie wyliczamy nieudokumentowanego
całkowitego kosztu wszystkich godzin rozwoju.

Zachowane niepowodzenia obejmują pierwsze zatrzymanie RF, kontrolę pamięci
wspólnego procesu pytest, błąd fixture przypięcia kodu przed kalibracją
oraz trzy błędy przygotowania final. [Indeks etapu](ai09-portfolio-ready.md)
wiąże receipts 09.130–09.135. Starsze diagnostyki capacity i wcześniejsze
próby development pozostają historią odrębnych protokołów.

## Decyzja, artefakty i odbiór

Zamrożona reguła wymagała zaliczenia globalnych kryteriów i wszystkich
wymaganych segmentów TF na każdym seedzie. Wynik to
`reject_for_new_promotion_retain_existing_portfolio`. Nie promowano RF/HGB
ani TF, nie zmieniono championów, progów AI 07–08 lub runtime.
Dotychczasowe karty i lifecycle mają własny, historyczny zakres.

Wykonanie: [Actions 38387579572](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/38387579572),
commit `8a84a9da75f000826c178d0dcbfbdd6664a19f3a`. Manifest 09.136 przypina
trzy archiwa i ich SHA-256, pełne raporty, wspólne populacje, koszty i dokładne
odtworzenie metryk. Artefakty Actions mają retencję 90 dni; lokalne kompletne
kopie są zachowywane w prywatnym `.local/portfolio-ready/` worktree etapu.
Źródła, modele i surowe predykcje nie są dodawane do Git.

R1–R7 mają dowody wykonania, a pełne archiwa są zweryfikowane.
`ready` wymaga jeszcze odbioru końcowego PR i wynikowego main przez Required CI oraz zgodnego planu Source.
Wynik naukowy pozostaje negatywny niezależnie od powodzenia tych kontroli.
