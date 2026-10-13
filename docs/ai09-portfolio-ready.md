# AI 09 — TensorFlow i konsolidacja portfolio

## Decyzja i pierwszeństwo

2026-10-13 użytkownik zatwierdził wariant 1 z audytu AI 09: „ready w
rekomendowanym wariancie (TensorFlow + konsolidacja portfolio)”. Warianty 2 i 3
mają pozostać możliwymi ulepszeniami, bez ich wdrażania w tym zadaniu.
Ta decyzja zastępuje zakres zamknięcia AI 09 opisany w starszych wpisach
STATUS i dokumentach wspólnej kampanii. Nie zmienia wyników dawnych prób,
nie usuwa ich kosztów i nie oznacza wykonania pierwotnej pełnej kampanii.

**Porównanie i konsolidacja zakończone; końcowy odbiór publikacji (R8).**
Trzy seedy final zakończyły się poprawnie, a wszystkie metryki odtworzono
dokładnie z oryginalnych predykcji i etykiet. [Raport końcowy](ai09-forecast-portfolio-report.md)
odrzuca TensorFlow do nowej promocji zgodnie z zamrożoną regułą. Gotowość
etapu nie wymaga wygranej TF ani kolejnej rundy treningu.

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

| ID | Wynik odbioru wykonania | Dowód |
| --- | --- | --- |
| R1 | Ukończone; modele, role, kalibracja, polityki, daty, seedy i budżety zamrożone przed final | [Protokół](reference/ai09-portfolio-assessment-v1.json), [zamrożenie](evidence/09-134-portfolio-calibration-complete.json), [plan final](reference/ai09-portfolio-final-v1.json) |
| R2 | Ukończone; oryginalny Source, 19443 wspólne klucze i 17437 kwalifikowanych etykiet w każdym seedzie | [Trzy populacje i lineage](evidence/09-136-portfolio-final-complete.json) |
| R3 | Ukończone; rzeczywisty RF, HGB i TF, stałe baseline oraz jedna konfiguracja rodziny | [Trening](evidence/09-132-portfolio-development-repair-complete.json), [karta TF](cards/tensorflow-portfolio.md) |
| R4 | Ukończone; seedy 42/137/2026, wagi po 1/3, bez ponownego fitu lub wyboru po final | [Oryginalne raporty i replay](evidence/09-136-portfolio-final-complete.json), [agregat](evidence/09-final/summary.json) |
| R5 | Ukończone; metryki i segmenty opisane, brakujące zero/high pozostają not_ready i blokują pozytywną rekomendację TF | [Raport jakości](ai09-forecast-portfolio-report.md) |
| R6 | Ukończone; rzeczywisty MLflow, świeży CPU reload, zasoby i zachowane niepowodzenia | [Karta TF](cards/tensorflow-portfolio.md), receipts [09.130](evidence/09-130-portfolio-development-attempt-1.json)–[09.136](evidence/09-136-portfolio-final-complete.json) |
| R7 | Ukończone; sprawdzone historyczne odbiory i artefakty, bez nowej kampanii lub promocji AI 07–08 | [Manifest](evidence/09-128-historical-portfolio.json), [portfolio](ai09-consolidated-portfolio.md) |
| R8 | Raport, karty i decyzja gotowe; publikacja wymaga poniższego końcowego odbioru | [Raport](ai09-forecast-portfolio-report.md), [zasady CI](contributing.md) |

Końcowy odbiór R8: pełny Required CI dokładnego PR AI oraz wynikowego main
(wszystkie 17 jobs success), następnie zgodny plan Source na odebranym main.
Dokładne AI PR/main i wyniki CI zapisuje końcowy receipt w
[dokumentacji Source](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/etapy/09-tensorflow-robustness.md).
Source podlega własnemu Required CI dla zakresu dokumentacji; celowe pominięcia
zadań spoza zakresu pozostają pominięciami. Receiptu nie dopisujemy do tego
samego commitu, którego hash i późniejszy wynik main dopiero powstają.
**Ready obowiązuje dopiero po tych odbiorach**, bez ponownego eksperymentu.

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

## Dlaczego etap trwał długo i co przyspiesza jego zamknięcie

- Wcześniejszy zakres łączył trzy zastosowania, pełne profile i rozwój
  brakujących adapterów. Wykonano wiele komponentów, lecz ich testy nie
  zastępowały końcowego eksperymentu. Zatwierdzony wariant 1 ograniczył ten zakres.
- Duża diagnostyka capacity miała kolejne nieudane próby, w tym timeout przy
  12 GiB. Podniesienie limitu RAM samo nie usuwa kosztu CPU i przygotowania.
- Błędy pomiaru pamięci workera, fixture preflight i dwóch połączeń importu
  zatrzymały później mały przebieg. Naprawy mają testy regresji, jawny budżet
  i zachowane wcześniejsze koszty; nie wymagały zmiany modeli lub ich progów.
- Sam trening TF trwał 13,47 s. Przygotowanie Source, instalacja środowisk,
  transfer dużych artefaktów oraz pełny CI zajmują znacznie więcej czasu.
  Nie ma podstaw, by przyspieszać zamknięcie przez kolejne strojenie TF.
- Pozostała ścieżka to jeden odbiór końcowych dokumentów i kodu przez PR/main
  oraz zgodny plan. Nowe optymalizacje mają sens tylko przy konkretnej blokadzie
  tego odbioru. Warianty 2–3, duży capacity diagnostic i nowa promocja są odłożone.

## Przebieg i zachowana historia prób

Poniższa chronologia zachowuje stan wiedzy na kolejnych krokach. Aktualne
wyniki i warunki odbioru znajdują się powyżej; dawne zapowiedzi kalibracji
i final nie oznaczają dodatkowych prac do ponowienia.

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

[Końcowe wykonanie 09.136](evidence/09-136-portfolio-final-complete.json), run
`38387579572`, zakończyło wszystkie trzy seedy. Metryki, populacje i agregat
zweryfikowano niezależnie; modele i kalibratory pozostały niezmienione.
Kompletne kopie trzech archiwów mają zgodne rozmiary i SHA-256; raporty,
etykiety i predykcje użyte do replay są identyczne z zawartością tych ZIP.
TF narusza limit biasu 10% na każdym seedzie, a grupy zero/high nie mają
wymaganych próbek. Zgodna z protokołem decyzja odrzuca nową promocję TF;
nie otwiera treningu ani zbierania nowego final. Pozostaje odbiór R8.

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
