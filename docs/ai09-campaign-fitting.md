# AI 09 — audytowany trening forecastingu

`campaign_fit.fit_campaign_forecast` wykonuje osobny plan v15 dla RF, HGB
lub kompaktowego TensorFlow. Rezerwuje próbę w trwałym journal przed odczytem
danych, sprawdza zamrożony plan i zakończony eksport development, a następnie
uruchamia przygotowanie, trening i reload jako trzy odrębne, mierzone procesy.
Porażka zużywa próbę i zachowuje zmierzony koszt. Receipt powstaje przed
zakończeniem operacji; walidacja receipt nie udziela kolejnego uprawnienia
do odczytu etykiet, treningu lub oceny.

Indeks SQLite i macierze na dysku obejmują wszystkie kwalifikujące się klucze
train i early_stopping. Nie ma próbkowania ani wyboru łatwiejszej populacji
dla TensorFlow. Przekroczenie limitu populacji, indeksu, macierzy, modelu lub
zasobów odrzuca całą próbę. Nowe jawne limity tego planu nie zmieniają starych
limitów RF ani wcześniejszego challengera. Domyślny limit macierzy to 1 GiB;
pełna kampania wymaga osobnego zamrożenia i pomiaru odpowiednich zasobów.

Imputacja, skalowanie, vocabulary, fallback unknown i parametry historii są
fitowane wyłącznie na train. TF dostaje 28-dniową historię, wszystkie dostępne
wtedy cechy dla 14 horyzontów i maski braków. Maski wyników zachowują częściowo
kwalifikujące się okna. Dense 32/16 zwraca oddzielne mean i median dla każdego
horyzontu; normalizację targetu zapisano w artefakcie. Early stopping korzysta
wyłącznie z przeznaczonej do tego roli. Trening nie otwiera wyników tune,
calibration ani development_evaluation.

RF ma wyuczony mean; nie przedstawiamy go jako wyuczonej mediany. HGB trenuje
osobno mean i quantile 0.5. Drzewa mają typowany przenośny format oraz kontrolę
zgodności na całej populacji train. TF zapisuje wspierany MLflow Keras flavor
z podpisem wejścia i wyjścia. Wszystkie rodziny korzystają z przypiętego locka
środowiska CPU; seedy inicjalizacji są niezależne od seedów danych. Determinism
TF dotyczy tej samej platformy i wersji, bez deklaracji zgodności bitowej
między platformami.

Świeży proces CPU ładuje zapisany model i sprawdza zgodność predykcji na
wszystkich kwalifikujących się kluczach early_stopping. Mierzony jest trening,
cold load, inference, CPU, peak własnego drzewa oraz rozmiar artefaktu.
MLflow przechowuje parametry, rzeczywiste koszty i candidate artifact;
nieudany reload zachowuje status failed. `verify_campaign_forecast_bundle`
sprawdza wszystkie zapisane hashe, plan, encoding, zależności i powiązanie
z zakończonym fitem, bez dodatkowych etykiet lub refitu. Niezgodność blokuje
użycie artefaktu.

Dotychczasowa wspólna regresja miała 124 passed, a po dodaniu kontroli całego
bundle i prywatnych plików 22 kontrole danych/audytu przeszły w 11.24 s.
Obejmują brak wpływu early_stopping na encoding, pełne klucze także dla
częściowych horyzontów, limity bez cichego sampling, rezerwację przed I/O,
rozliczenie porażek i uszkodzony artefakt/receipt. Są to kontrole komponentów,
z jawnymi mockami supervisora w testach audytu. Wymagany `make tensorflow-check`
na dokładnym headzie `9b09660` zaliczył 6 testów w 101.69 s, w tym trzy nowe
rzeczywiste kontrole CPU fit/reload RF/HGB/TF. [Job](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37652743985/job/112900689298)
obejmuje zapis MLflow, całą kontrolną populację i odrzucenie uszkodzonych
predykcji. Pełne CI tego headu zakończyło się porażką: cztery zadania nie
otrzymały runnera po pięciu próbach przydziału; piątym nieudanym zadaniem był
agregator. Nie rozpoczęto testów w tych czterech zadaniach, ich koszt jest
nieznany. Wynik CPU i porażka infrastruktury pozostają zapisane. Lokalny odczyt dostępnej RAM
wykazał około 1.24 GiB, poniżej wymaganej sumy limitu 1 GiB i rezerwy 1 GiB,
więc nie rozpoczęto tu procesów tych kontroli.

Przygotowanie wspólnego scoringu używa teraz tych samych funkcji budowania
wektorów w treningu i predykcjach. Nie uczy ponownie encoding ani target scale.
Konwersja TF zachowuje mean/median, wszystkie 14 horyzontów i oryginalne
jednostki sprzedaży. Dodatkowa kontrola rzeczywistego MLmodel sprawdza CPU
backend, podpis float32 o dokładnej szerokości wejścia i wyjściu 14 × 2 oraz
digest planu przed załadowaniem frameworka. 48 kontroli danych/audytu/wektorów
i podpisu oraz Mypy 671 plików przeszły. Head `ab122989` ma pełny
[Required CI 37659317202](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37659317202):
17/17 success wraz z required-result. Jego rzeczywisty job CPU zaliczył
6 testów w 102.64 s, obejmujących również odrzucenie uszkodzonego podpisu
MLmodel. Są to kontrolowane dane, bez projektowego treningu lub kwalifikacji.
Wspólne raw scoring i kalibracja mają osobny zakres odbioru.

PR #37 jest skierowany bezpośrednio na main. Nowa integracja obejmuje scalony
eksporter PR #36 oraz poprawkę snapshotu z [receipt 09.26](evidence/09-26-generation-snapshot-verification.json).
40 testów generation/fit/data oraz Mypy 671 plików przeszły po integracji.
Wspólna integracja PR39 `8497908d` zaliczyła własny Required CI 17/17
success i została chronioną ścieżką scalona na main `5ed0544e`.
PR37 został automatycznie oznaczony merged; CI dokładnego main jest w toku.

[Evidence przygotowania](evidence/09-23-campaign-fitting-preparation.json)
zachowuje wcześniejsze porażki kontroli i zakres dowodu. Projektowy journal
pozostaje niezainicjalizowany, liczba nowych projektowych fitów wynosi zero,
final test pozostaje nieotwarty. Scoring na wspólnych kluczach, uczciwy wybór
konfiguracji i kalibracja, pozostałe dwa zastosowania, pełna kampania trzech
seedów, robustness/niepewność, lifecycle, karty/raporty i odbiór dokładnego
main nadal są wymagane przed AI 09 ready.
