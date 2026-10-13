# AI 09 — TensorFlow i konsolidacja portfolio

## Decyzja i pierwszeństwo

2026-10-13 użytkownik zatwierdził wariant 1 z audytu AI 09: „ready w
rekomendowanym wariancie (TensorFlow + konsolidacja portfolio)”. Warianty 2 i 3
mają pozostać możliwymi ulepszeniami, bez ich wdrażania w tym zadaniu.
Ta decyzja zastępuje zakres zamknięcia AI 09 opisany w starszych wpisach
STATUS i dokumentach wspólnej kampanii. Nie zmienia wyników dawnych prób,
nie usuwa ich kosztów i nie oznacza wykonania pierwotnej pełnej kampanii.

**Bieżący status: in_progress / not_ready.** Zatwierdzono zakres, nie wyniki.

## Warunki zamknięcia

| ID | Wymaganie | Dowód wymagany do ready |
| --- | --- | --- |
| R1 | Zamrożony protokół nowego porównania forecastingu | Wersja kodu i producenta, locki, nazwany profil, daty, trzy seedy danych 42/137/2026, seed treningu, scenariusze, role, budżety, polityki metryk i decyzji zapisane przed odpowiednimi odczytami i fitami |
| R2 | Rzeczywiste, poprawne dane | Oryginalny Source → snapshot → curated → features; pozytywna kwalifikacja, lineage, liczności, dojrzałe etykiety i wspólne oceniane klucze; brak zastępczych metadanych z testów |
| R3 | Baseline, RF, HGB i kompaktowy TensorFlow | Jedna zamrożona konfiguracja każdej rodziny, jawny skończony budżet, train-only preprocessing, horyzonty 1–14 i rzeczywiste dopasowanie; wcześniejsze strojenie i koszty wskazane jako kontekst |
| R4 | Niezależna ocena końcowa | Wybór i kalibracja zamknięte przed final, weryfikacja wcześniejszej ekspozycji, trzy wyniki per seed oraz agregat z wcześniej ustalonymi wagami; słaby wynik nie uruchamia kolejnego strojenia |
| R5 | Jakość i ograniczenia | Punktowe różnice na wspólnych kluczach, zmienność między seedami, bias i coverage przedziałów, liczności i ważność metryk w zadeklarowanych segmentach; brak próbek nie oznacza passed |
| R6 | Artefakty i rzeczywiste koszty | Model TF z preprocessingiem i wspieranym formatem MLflow, run ID, kontrola odtworzenia na CPU w nowym procesie, czas treningu/odtworzenia/inferencji, RSS i rozmiar; wszystkie próby i błędy zachowane |
| R7 | Konsolidacja AI 07–08 | Przypięte dotychczasowe odbiory, karty, raporty, model IDs, polityki i ograniczenia; jawne oznaczenie historycznej oceny na jej własnych danych, bez deklaracji nowej wspólnej kampanii |
| R8 | Decyzja i odbiór projektu | Raport RF/HGB/TF/baseline, aktualna karta forecastu oraz przypięte karty anomaly/stockout, decyzja retain/reject, istniejący odbiór lifecycle wskazany z zakresem; kontrole zmienionego kodu, pełne Required CI dokładnego PR i wynikowego main oraz zgodna dokumentacja planu |

R1–R6 i R8 pozostają otwarte do wskazania i sprawdzenia oryginalnych dowodów.
R7 ma [sprawdzone powiązania artefaktów](evidence/09-128-historical-portfolio.json)
i [zestawienie portfolio](ai09-consolidated-portfolio.md); końcowy odbiór
publikacji tej dokumentacji należy do R8.
Liczba zaliczonych testów komponentów nie jest procentem gotowości kampanii.
Ready etapu nie oznacza, że TensorFlow został championem lub dopuszczono nową
promocję. Odrzucenie TF jest poprawnym wynikiem zakończonego porównania.

## Ograniczenia zaakceptowanego wariantu

- Jedna skala development; bez kaskady 25/50/100 i nowego strojenia anomaly/stockout.
- Nowy mniejszy profil o własnej nazwie, nie podszywający się pod `ai-training`.
  Szerokość panelu dobieramy na podstawie kwalifikacji danych development;
  zachowujemy potrzebną historię, dojrzałość etykiet i trzy seedy final.
  Dokładne wymiary, daty, segmenty i minimalne liczności muszą zostać ustalone
  przed kampanią końcową; ta decyzja zakresowa nie zastępuje protokołu danych.
- Bazowa konfiguracja każdej rodziny jest stała. Early stopping TF korzysta
  wyłącznie z wydzielonego development; kalibracja przedziałów korzysta z osobnej
  roli. Reguła wyboru i obsługa odrzucenia są zamrożone przed final.
- Różnice metryk i zmienność między seedami wystarczają do zakresu niepewności;
  rozbudowane bootstrap CI dla delty metryk są odłożone. Ocena przedziałów
  prognozy i ich coverage pozostaje wymagana.
- AI 07 i AI 08 zachowują własne zaakceptowane wyniki. Ich stare dowody nie
  stają się nowymi pomiarami ani dowodem świeżości danych AI 09.
- Nie wykonujemy nowej promocji produkcyjnej. Raport przypina aktualne decyzje
  i istniejący odbiór lifecycle; nowa promocja wymagałaby własnego zakresu.
- Pełny canonical development capacity diagnostic starego profilu nie jest
  bramką tego wariantu. Zasoby mierzymy podczas nowego rzeczywistego przebiegu.
  Limit drzewa procesu do 12 GiB i rezerwa hosta 1 GiB pozostają zachowane;
  lokalny komputer z 8 GiB nie otrzymuje od tej deklaracji dodatkowej pamięci.

## Kolejność pracy

Pierwszy zamrożony przebieg opisuje
[receptura development v1](reference/ai09-portfolio-development-v1.json):
profil eksperymentu `ai09-portfolio-development-1`, jawny Source `ai-load`,
365 dni od 2026-01-01 do 2026-12-31, 8 produktów, 3 pary sprzedaży,
2 lokalizacje zapasu, seed 42. To 8760 wierszy panelu dziennego; nie jest
to liczba wszystkich zdarzeń Source ani rekordów cech wielohoryzontowych.
Trening obejmuje originy lipiec–wrzesień, early stopping październik–listopad;
etykiety mają co najmniej 15 dni dojrzałości, role są rozdzielone purge.
Wydzielony development holdout nie jest final. W tym pierwszym przebiegu
porównujemy punkty na walidacji użytej do early stopping, więc raport jest
diagnostyczny; kalibracja i niezależna ocena R4–R5 pozostają do wykonania.

Polecenie `scripts/run_ai09_portfolio_development.py` wykonuje oryginalne
generowanie, kwalifikację, eksport, import i curation, buduje niepuste cechy
i role, następnie używa istniejącego porównania baseline/RF/HGB/TF.
Nie kopiuje generatora ani nie zmienia starszego walidatora kampanii.
Każdy fit ma jedną próbę; nieudany przebieg wymaga jawnej decyzji i nowej
wersji planu, z zachowaniem jego kosztu. Wykonanie odbywa się w trybie
`portfolio_development` istniejącego workflow na izolowanym Linux, bez
ingerowania w lokalne sesje. Dokument i kod nie stanowią dowodu wykonania.

Pierwszy rzeczywisty przebieg zakończył pięć faz przygotowania w 432,52 s
oraz utworzył niepuste role. Porównanie modeli zatrzymało się na pierwszym RF;
nie rozpoczęło TensorFlow. [Dowód próby 1](evidence/09-130-portfolio-development-attempt-1.json)
zachowuje błąd i koszt kolejnych 746,85 s. Całe drzewo procesu osiągnęło około
2,59 GiB; nie przekroczyło limitu 12 GiB. Podejrzewany błąd dotyczy starszego
pomiaru RSS workera na Linuksie, nie jakości modelu; brak zachowanego końcowego
pomiaru tego workera uniemożliwia uznanie przyczyny za bezpośrednio udowodnioną.

[Jawny plan naprawy](reference/ai09-portfolio-development-repair-v1.json)
dopuszcza jedną dodatkową próbę porównania na dokładnie tych samych danych,
po teście regresji na Linuksie i natywnym teście TF. Zachowuje konfiguracje
modeli i ich limity. Pobiera ZIP o przypiętym SHA-256, nie generuje ponownie
Source ani nie zeruje historii niepowodzenia. Nowy budżet wykonania naprawy
wynosi 60 minut, obok zachowanego kosztu pierwszej próby. Wynik development
nadal nie zastępuje osobnej kalibracji i końcowej oceny trzech seedów.

[Naprawa zakończona](evidence/09-132-portfolio-development-repair-complete.json):
run `38378609989`, 34 kontrole Linux passed bez pominięć, pełne porównanie
wszystkich modeli na 9408 wspólnych kluczach (8400 kwalifikowanych).
ZIP i wszystkie 75 plików porównania zostały zweryfikowane. Source nie był
ponownie generowany. Czasy workerów: RF 10,71 s, HGB mean 3,97 s, HGB median
4,58 s, TF 18,00 s; cała naprawa z transportem 582,30 s i około 2,75 GiB
zmierzonego szczytu drzewa procesu. TF ma oryginalny run MLflow oraz odtworzenie
na CPU. To zamyka wykonanie development R3 i część artefaktową R6;
nie jest wynikiem kalibracji ani końcowej kwalifikacji jakości.

[Protokół kalibracji i oceny końcowej](reference/ai09-portfolio-assessment-v1.json)
ustala TF jako jednego challengera i `history28` jako stały punkt odniesienia.
RF, HGB oraz dwa pozostałe baseline pozostają porównaniami. Oddzielna rola
28 listopada–11 grudnia służy wyłącznie kalibracji: 90% przedziały z osobnym
promieniem na model i horyzont, co najmniej 100 etykiet i 80% kwalifikowanych
wierszy. RF ma wyłącznie średnią; przedział wokół niej nie zmienia jej w medianę.

Dopiero kompletny artefakt zamrożenia pozwoli uruchomić nowy Source dla seedów
42/137/2026: 365 dni 2026-04-01–2027-03-31, ta sama szerokość 8 × 3,
originy końcowe 2027-01-17–2027-03-15 i cutoff etykiet 2027-03-30.
To daty symulacji, nie zebrane obserwacje przyszłości. Wszystkie modele mają
te same oceniane klucze; agregat nadaje każdemu seedowi wagę 1/3.
Brak próbki w grupie pozostaje `not_ready`, a słaby wynik kończy się
odrzuceniem TF bez ponownego strojenia. Dane końcowe mogą zostać wygenerowane dopiero po zweryfikowaniu artefaktu zamrożenia.

Jawnie zachowujemy ograniczenie starszej, utraconej historii: znane wcześniejsze
próby AI09 dotyczyły danych do lipca 2026, nowy development do grudnia 2026,
ale nieudokumentowanego dawnego użycia nie da się uznać za zero. Świeżość oceny
będzie oparta na nowym, prospektywnym wykonaniu i rozłączności z konkretnymi
danymi dopasowania i kalibracji; nie deklarujemy globalnego dowodu wszystkich
dawnych odczytów. [Plan kalibracji](reference/ai09-portfolio-calibration-v1.json)
przypina dokładny wynik naprawy, zachowuje jedną próbę i nie dopuszcza nowych fitów.

[Kalibracja została wykonana](evidence/09-134-portfolio-calibration-complete.json)
w run `38381951994`: 279–304 kwalifikowane obserwacje na każdy z 14 horyzontów,
wszystkie promienie dopasowane, zero nowych fitów modeli. Artefakt zamrożenia
`11804525844` ma zweryfikowany ZIP oraz 73 pliki modeli. Jego oryginalny commit,
kalibrator i cztery pliki implementacji są przypięte w
[planie końcowego wykonania](reference/ai09-portfolio-final-v1.json).
Wykonanie końcowe zachowuje oryginalne role `final_evaluation`, rezerwuje cały
nowy parent przed generowaniem oraz ponownie weryfikuje snapshot i pełną
transformację curated. Trzy seedy wykonują się na trzech odrębnych runnerach;
każdy ma jedną próbę i własny limit 12 GiB. Kod nie tworzy nowych modeli.
Po wyniku final nie zmieniamy wyboru, kalibracji ani progów.

[Pierwsza próba final](evidence/09-135-portfolio-final-attempt-1.json), run
`38384080156`, zachowała trzy kompletne przygotowania Source/snapshot/curated.
Każde zaliczyło pięć faz, po czym przygotowanie oceny zatrzymał błędny poziom
katalogu importu: manifest jest w podkatalogu `snapshot`. Żaden seed nie
rozpoczął inferencji ani oceny jakości; dostęp do całego final pozostaje
ujawniony. Nowy test rzeczywistego importu wykrył też niewłaściwy import
funkcji przypinającej runtime replay. Obie pomyłki poprawiono bez zmiany
danych, modeli, kalibracji, segmentów i metryk.

[Jawny plan naprawy final](reference/ai09-portfolio-final-repair-v1.json)
przypina trzy oryginalne archiwa, ich SHA-256 oraz receipts. Dopuszcza po
jednym dodatkowym wykonaniu przygotowania oceny, inferencji i scoringu;
nie uruchamia ponownie generatora ani treningu. Oryginalne archiwa, koszty
i błędy są zachowane razem z nowym wynikiem. To kontynuacja tych samych
zarezerwowanych danych, nie deklaracja nowego nietkniętego final.
Pełny ZIP kalibracji dodatkowo pobrano i sprawdzono; niezależne obliczenie
przedziałów z oryginalnych predykcji i etykiet dało dokładnie ten sam wynik.

1. Przypiąć zakres i zinwentaryzować istniejące dane, modele i historię ekspozycji.
2. Zamrozić mały development i wykonać jeden oryginalny przebieg od Source,
   przez niepuste role i model, do odtworzenia oraz raportu. Zachować koszty.
3. Ukończyć porównanie wszystkich rodzin i osobną kalibrację, zamrozić wybór.
4. Dopiero wtedy wygenerować i ocenić zadeklarowane świeże źródła końcowe.
5. Utrwalić portfolio, ograniczenia i decyzje; odebrać dokładny kod PR/main.

Jedna gałąź integracyjna AI `ai/09-portfolio-ready`; odpowiadająca gałąź
repozytorium platformy aktualizuje plan. Starszych sesji i procesów nie
przerywamy. Lokalne kontrole dotyczą zmienionych zachowań; pełny Required CI
na Linux pozostaje wymagany. Nie powtarzamy obowiązkowo całego `ci-local`
na Macu przed każdym PR tej kampanii. Zobacz [zasady zmian](contributing.md).
Optymalizujemy wyłącznie wykazany problem bieżącego przebiegu lub blokadę
R1–R8, bez otwartej listy „wszystkich możliwych optymalizacji”.

## Możliwe dalsze ulepszenia — poza tym zadaniem

### Wariant 2: nowa ocena trzech zastosowań na mniejszym profilu

Nowa, wspólna ocena forecastingu, anomaly i stockout na świeżych danych.
Wymaga natywnych weryfikatorów ocen anomaly/stockout, właściwego powiązania
ich modeli i ról, integracji wyboru/kalibracji/progów oraz kompletnych
liczności wymaganych grup. Ponowne dopasowanie zamrożonych receptur tylko,
gdy wymaga go zgodność cech lub chronologia; bez automatycznego szerokiego
strojenia. Mniejszy profil, jedna skala development i skończony budżet prób.
**Status: propozycja, nie wdrażać w ramach zatwierdzonego celu.**

### Wariant 3: pełna wcześniejsza kampania

Pełne profile, portfolio ordinary/demand/physical dla development i trzech
seedów final, wspólna końcowa kwalifikacja trzech zastosowań oraz wcześniej
zaplanowane rozszerzone próby i analizy. Wymaga osobnego budżetu, wykazanej
pojemności i ukończenia integracji opisanych w starszych dokumentach.
Zachowane istniejące komponenty nie są deklaracją wdrożenia tego wariantu.
**Status: propozycja, nie wdrażać w ramach zatwierdzonego celu.**

Źródła historycznego zakresu: [kampania](prospective-evaluation-campaign.md),
[pełne portfolio](ai09-full-scenario-portfolio.md),
[capacity](ai09-development-capacity.md). Ich blokady i wyniki pozostają
prawdziwe dla tamtych protokołów; nie usuwamy walidatorów, aby udawać ich sukces.
