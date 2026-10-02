# Protokół jakości 2.0 — mediana, średnia i przedziały

Status: protokół zamrożony przed nową oceną. To zmiana
zasad oceny, nie ponowna kwalifikacja modelu ani zamknięcie blokad AI 04.
Dotychczasowa [wersja 1](forecast-quality.md), wszystkie wyniki i blokady
kampanii v10 pozostają niezmienione. Nowych danych nie generowano, nowych
holdoutów nie oceniano. [Spis zachowanych plików](evidence/04-quality-protocol-v2-retained.json)
wiąże 3070 plików, w tym wyniki, źródło, snapshot i oba raporty próby v10,
sumami SHA-256. Pliki pozostają na obecnym dysku; nie jest to kopia zapasowa
na drugim nośniku.

## Dlaczego zmieniamy reguły

1. W wersji 1 poprawna prognoza samych zer nie może zaliczyć oceny, ponieważ
   WAPE, normalized bias i width/mean mają zerowy mianownik. Jednocześnie MAE,
   błąd w sztukach i pokrycie przedziału są całkowicie określone.
2. Dla 99 zer i jednej sprzedaży 1 sztuki przedział [0,1] ma width/mean=100.
   Iloraz miesza niepewność z bardzo małą częstością sprzedaży; sam duży wynik
   nie dowodzi, że przedział jest bezużytecznie szeroki.
3. MAE jest zgodny z medianą rozkładu, a błąd kwadratowy ze średnią. Wymaganie
   jednocześnie optymalnego MAE i małego bias od jednej liczby może być sprzeczne.
   Dla P(Y=1)=0,1 i P(Y=0)=0,9 mediana 0 ma MAE=0,1 i normalized bias=-100%,
   a średnia 0,1 ma MAE=0,18 i bias=0. Zmniejszenie bias zwiększa tu MAE o 80%.

Dobór miary do celu prognozy uzasadnia
[Gneiting, Making and Evaluating Point Forecasts](https://arxiv.org/abs/0912.0902).
Wybór dwóch jawnych wyników został potwierdzony przez użytkownika przed
oceną nowych danych. Nie przeliczaliśmy wyników nowych holdoutów, aby dobrać
reguły lub progi.

## Jawne cele i zachowane wymagania

[Kontrakt](../contracts/forecast/v2/quality.default.json) ma wersję
`forecast-quality-2.0.0`. Każdy klucz wymaga mediany, średniej oraz dolnego
i górnego kwantyla przedziału centralnego 90%, a także odpowiadających im
zamrożonych baseline'ów. Pełna kwalifikacja wymaga **obu prognoz punktowych**.
Brak którejkolwiek daje `not_ready`; nie kopiujemy automatycznie pojedynczej
starej predykcji do obu pól. Dwie wartości mogą się równać, gdy uzasadnia to
rozkład, ale ich cel, metoda i wybór muszą być zadeklarowane przed testem.

| Oceniany wynik | Reguła 2.0 |
|---|---|
| Mediana | MAE: globalnie poprawa **>5%**, w krytycznych segmentach regresja **≤10%** |
| Zachowanie baseline'u mediany | Bez wymagania poprawy wobec samego siebie; wszystkie predykcje muszą być identyczne |
| Baseline z MAE=0 | Kandydat z MAE=0 zalicza remis; każdy dodatni błąd jest pogorszeniem, bez dzielenia przez zero |
| Średnia | Absolutny normalized bias **≤10%**; dodatkowo MSE nie może być gorsze od baseline'u średniej |
| Pokrycie przedziału | **≥80%**, nominalnie **90%** |
| Przydatność przedziału | Średni interval score nie gorszy od zamrożonego baseline'u przedziałów |
| Próba | Minimum **100** globalnie, **30** w każdym wymaganym segmencie |
| Dostępność | **100%** wymaganych prognoz; eligibility **≥80%**; minimum **50** reszt kalibracji |

Nie podwyższono progów MAE, bias, coverage ani minimalnej próby. Stałe kontraktu
są walidowane: próba zmiany np. regresji MAE na 11% jest odrzucana. Nowa kontrola
MSE przeciwdziała pozornej poprawności średniej przez wzajemne znoszenie się
dużych błędów dodatnich i ujemnych. Bias sam w sobie nie jest miarą dokładności.

MAE prognozy średniej i bias prognozy mediany nadal są raportowane wraz ze
wskazaniami przekroczenia starych limitów, lecz nie są zamieniane rolami.
Stare **29 failed / 8 not_ready** receptury 2.1 nie znikają i nie otrzymują
nowej etykiety `passed`. Nowa ocena powstanie w osobnym artefakcie. Rzeczywiste
pogorszenie MAE mediany, MSE średniej, bias średniej, coverage lub interval
score daje odpowiednią przyczynę `failed`. Jeśli równocześnie brakuje próbki
lub prognozy, wynik jest `not_ready`, ale lista wykrytych błędów i flaga
`has_measurable_failures` pozostają w raporcie.

## Zera i małe mianowniki

Koszyk wolumenu `zero` nadal oznacza zerową średnią historii **znanej w origin**.
Nie jest definiowany wynikiem przyszłej sprzedaży. Segment może mieć dodatnie
actuals mimo zerowej historii. Osobną sytuacją jest suma actuals=0 w ocenianej
próbce — reguła poniżej dotyczy każdej takiej próbki, bez wybierania korzystnych
wierszy po poznaniu wyniku.

Przy sumie actuals=0 WAPE, normalized bias i width/mean pozostają `null`,
nie zero. Ich nieokreśloność nie blokuje poprawnych zer. Oceniane są błędy
w sztukach, false positive units i interval score. Dodatnia prognoza średniej
przy samych zerach daje `positive_mean_forecast_on_all_zero_actuals`; tolerancja
w sztukach wynosi tu zero, zgodnie z zerowym dopuszczalnym błędem 10% × suma
actuals. Nie oznacza to, że przyszła sprzedaż po historii zer musi być zerowa.
Brak etykiety i wyłączony wiersz nigdy nie stają się zaobserwowanym zerem.

Nie dodajemy epsilonu ani arbitralnego minimum mianownika. Width/mean nadal
jest raportowane, z flagą przekroczenia starego limitu 2, ale przestaje być
bramką jakości. Dla przedziału [L,U], actual y i α=0,1:

`IS = U − L + (2/α)·max(L − y, 0) + (2/α)·max(y − U, 0)`.

To miara w sztukach: szeroki przedział płaci za szerokość, zbyt wąski za
nietrafione obserwacje. Regułę i jej zgodność z centralnymi kwantylami opisują
[Gneiting i Raftery, sekcja 6.2](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf).
Porównujemy średnie score na tych samych kluczach, bez ilorazu i bez tolerancji
regresji. Zerowy score baseline'u wymaga zerowego score kandydata. Coverage
pozostaje osobną bramką: wąski przedział nie może ukryć niedostatecznego pokrycia.
Średnia rozkładu nie musi leżeć między jego kwantylami 5% i 95%; mediana musi.

Nie deklarujemy bezwzględnej biznesowej opłacalności przedziału na podstawie
samego porównania z baseline'em. Do takiego warunku potrzebne są jawne koszty
niedoboru i nadmiaru, których nie zastępujemy nowym arbitralnym progiem.

## Zamrożenie i granica implementacji

Moduł `quality_v2.py` jest działającym, testowanym evaluatorem pojedynczego
segmentu. Nie uruchamia modeli i nie kwalifikuje sam całej kampanii. Istniejący
runner v10 nadal używa wersji 1; nie podmieniamy jego zależności ani konfiguracji.
[Zamrożenie](../contracts/forecast/v2/quality.freeze.json) wiąże reguły, kod,
testy, lock zależności, zachowane źródło/snapshot, raport v10 i plan okien.

Przed pełną kampanią nowy runner musi:

1. Zamrozić metody i konfiguracje mediany, średniej i kwantyli. Wybierać baseline'y
   oddzielnie na validation według MAE, MSE i interval score, z ustaloną kolejnością
   rozstrzygania remisów. Nie wybierać modelu na holdoucie.
2. Sprawdzić pochodzenie predykcji, as-of, cutoff kalibracji, minimum reszt oraz
   kompletny spis segmentów: horyzonty, kategorie, kanały i wszystkie cztery koszyki
   zero/low/medium/high. Brak wymaganego segmentu blokuje pełną kwalifikację.
3. Związać każdą predykcję i baseline z niezmiennymi kluczami, osobno ocenić foldy
   i pooled z surowych sum; nie uśredniać wskaźników między foldami.
4. Ponownie sprawdzić zamrożenie, nienaruszenie starych artefaktów i zasoby.
   Zapisać osobny nowy identyfikator kampanii i jej konfigurację przed oceną.

Kandydatem pozostają niewykorzystane holdouty v10: 29 lipca–11 sierpnia,
12–25 sierpnia i 26 sierpnia–8 września 2026, ten sam snapshot. Są to kolejne
okna syntetycznego development, nie portfolio final test ani dowód jakości
produkcyjnej. Po ich ocenie nie wolno zmieniać reguł i nazywać ponownej oceny
niezależnym testem. Ten etap kończy się zamrożeniem protokołu, bez pełnego biegu.

## Miejsce przed pełnym biegiem

Poprzedni szacunek 3,904 GB rozwiniętych wejść zakładał tylko podwojenie liczby
sklepów. Nowy kalendarz ma także **100 zamiast 91 originów**. Skalowanie starego
pomiaru 1952151135 bajtów daje **4290442055 bajtów**, dla maksymalnie 20000 historii
i 280000 wierszy cech. To około 3,996 GiB przy limicie 4 GiB; 10% niepewności
przekracza limit. Ten problem trzeba rozwiązać lub dokładnie sprawdzić przed
treningiem, bez obcinania populacji. Limit nie został podniesiony w tej zmianie.

Writer kompresuje tymczasowy indeks, lecz `verify_inputs` nadal zapisuje całe
kanoniczne rekordy do SQLite. Sama kompresja plików Parquet nie określa więc
potrzebnego wolnego miejsca. Konserwatywny budżet dodatkowego miejsca:

| Składnik | Rezerwa |
|---|---:|
| Nowe trwałe curated, cechy, predykcje i raporty | 3 GiB |
| Nieskompresowany indeks weryfikacji z narzutem | około 6 GiB |
| Inne współistniejące indeksy | 2 GiB |
| Dzienniki SQLite i sortowanie | około 2 GiB |
| Kopia wejścia i staging odtworzenia | 1 GiB |
| Zapas dla systemu i błędu estymacji | 2 GiB |
| **Wymagane wolne miejsce przed startem** | **16 GiB** |

To rezerwa planistyczna dla jednego sekwencyjnego biegu na wspólnym filesystemie,
nie zmierzony szczyt ani gwarantowane maksimum nieograniczonych indeksów. Stare
artefakty są już zapisane i nie są usuwane w celu uzyskania tego budżetu. Nie ma
nowej generacji źródła. Przed biegiem trzeba ponownie zmierzyć wolne miejsce
zarówno dla workspace, jak i systemowego katalogu tymczasowego.

```bash
.venv/bin/python scripts/forecast_quality_v2_preflight.py \
  --verify-retained --output reports/quality-v2-preflight-NEW.json
```

Polecenie tylko czyta zachowane pliki i zapisuje nowy raport; odrzuca istniejący
plik wyjściowy. Exit 3 oznacza niegotową pełną kampanię. Nawet wystarczające
miejsce nie zastępuje brakujących kontraktów predykcji i kontroli limitu wejść.

[Wykonany preflight](evidence/04-quality-protocol-v2-preflight.json) potwierdził
niezmienność wszystkich 3070 plików i zgodność zamrożenia. W chwili pomiaru
dostępne było 41033883648 bajtów (około 38,2 GiB), powyżej budżetu 16 GiB.

## Weryfikacja tej zmiany

**26 testów przeszło** w 47,45 s: 11 małych testów nowego protokołu i przygotowania
oraz istniejące testy jakości v1 i receptury 2.1. Przykłady obejmują poprawne
zera, fałszywy popyt, rzadką sprzedaż, zbyt szerokie i zbyt wąskie przedziały,
konflikt średniej z medianą, znoszenie bias mimo pogorszenia MSE, brak prognozy,
niezmienione granice MAE oraz wykrycie naruszenia zachowanych plików i zamrożenia.
Raport testów: `reports/ai04-quality-protocol-v2-regression.xml`.
Ruff, formatowanie, mypy, spójność schematów i odsyłacze dokumentacji przeszły.
Testy nie używają nowego snapshotu do dopasowania modeli ani do doboru progów.
[Podsumowanie maszynowe](evidence/04-quality-protocol-v2.json) wiąże freeze,
preflight, wyniki testów i odrębną paczkę w `dist/quality-v2`. Izolowana kontrola
wheela potwierdziła oba schematy i zaliczenie poprawnego przypadku zerowego;
poprzedni wheel zachował swoją sumę SHA-256.
