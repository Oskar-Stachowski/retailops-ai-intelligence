# Pełny trening numeryczny anomaly

Przygotowano korektę pomiaru RSS workera na Linuksie. `getrusage()` zachowuje
statystyki sprzed `execve`, więc wynik dziecka może zawierać pamięć procesu
uruchamiającego. Dodanie jej do osobnego szczytu rodzica zawyża sumę.
Worker census odczytuje teraz `VmHWM` bieżącego obrazu procesu; brak lub
niejednoznaczny odczyt blokuje wynik. Monitor nadal mierzy całe żywe drzewo,
a końcowy limit nadal obejmuje sumę szczytu rodzica i właściwego workera.
Limit 1 GiB i rezerwy treningu oraz limit canonical 12 GiB pozostają bez zmian.
Przekroczenie końcowego budżetu zachowuje w błędzie wartości pomiaru.

Semantykę opisują [Linux getrusage](https://www.man7.org/linux/man-pages/man2/getrusage.2.html)
i [dokumentacja proc](https://www.kernel.org/doc/html/latest/filesystems/proc.html).
Lokalnie przeszły 84 kontrole; dwie rzeczywiste regresje Linux zostały pominięte
na macOS i wymagają odbioru na Linuksie. Tryb `census_memory` istniejącego małego
workflow wykonuje kontrolę fork/exec, prawdziwy fit z 600 MiB danych rodzica,
pełne testy census i trzy istniejące publiczne rodziny Source.
Nie uruchamia generatora, Project ani final. Zdalny wynik jest jeszcze nieznany.

[Pełna kontrola lokalna 09.75](evidence/09-75-full-fit-local-validation.json)
na kodzie `cf8fc3f` zakończyła się niepowodzeniem: główny zestaw ma
5120 zaliczonych i 49 pominiętych testów, lecz pięć testów TensorFlow
odmówiło startu z powodu `preflight_reserve` (trzy pozostałe przeszły).
Nie obniżono rezerw i nie zalicza się tego przebiegu jako pełnego sukcesu.
Pełna akceptacja wymaga Required CI dokładnego head i wynikowego main.

`fit_census_pipeline` usuwa ograniczenie 10000 wierszy dla nowej, jawnej
wersji modelu AI 09. Przyjmuje cały strumień zadeklarowanych, uprawnionych
wierszy treningowych i jego dokładną liczność. Brakujący lub dodatkowy wiersz
przerywa operację przed startem workera. Nie wybiera podzbioru treningowego.
Wbudowane losowanie obserwacji przez Isolation Forest zachowuje oryginalny
algorytm i zamrożone `max_samples`, seed, liczbę drzew oraz parametry modelu.

Macierz float64 powstaje na dysku. Mediany wykorzystują wszystkie znane
wartości train; brakujące wartości otrzymują medianę albo jawne zero przy
całkowitym braku danych. Osobne wskaźniki braków pozostają w macierzy.
Strumieniowy hash ma tę samą tożsamość co oryginalna pełna tablica JSON.
Próby walidacyjne nie wpływają na imputację ani fit. Worker sprawdza rozmiary,
kształty, skończone wartości i hashe macierzy przed i po treningu.

Rzeczywisty sklearn Isolation Forest oraz eksport przenośnych drzew korzystają
z tej samej funkcji co dotychczasowy worker. Kontrola odtworzenia obejmuje
wejścia treningowe, osobne próby i wartości float32 wokół granic drzew,
z tolerancją 1e-12. Model 4.0.0 zachowuje cechy count-rate i działa z natywnym
scorerem obu rodzin. Wskazuje pełny census przez hashe cech; nie deklaruje
identyfikatora starego, ograniczonego artefaktu qualified-anomaly-inputs.

Nowa polityka dopuszcza do 1000000 wierszy i 256 MiB macierzy, przy 300 sekundach
CPU/czasu oraz 1 GiB pamięci treningu. Monitor sprawdza proces wywołujący i
własnego workera; końcowa pamięć uwzględnia konserwatywną sumę ich szczytów.
Wymagana rezerwa hosta wynosi 1 GiB RAM i 6 GiB wolnego dysku. Przed zapisem
macierzy sprawdzane jest dodatkowe miejsce na własne artefakty. Przekroczenie
budżetu zatrzymuje wyłącznie utworzoną przez tę funkcję grupę procesów.
Budżet diagnostyki canonical nadal wynosi 12 GiB / 8 GiB scratch / 3600 sekund.

`census_capacity_threshold` uwzględnia wszystkie zadeklarowane wyniki
walidacyjne. Oblicza oryginalną statystykę pozycyjną na macierzy dyskowej,
z dokładnie tym samym limitem alertów i regułą ścisłego przekroczenia progu.
Remisy nie zwiększają liczby alertów. Brakujące, nadmiarowe i niepoprawne
wyniki blokują próg; niedostateczna próba zachowuje brak progu.

[Kontrole 09.68](evidence/09-68-native-anomaly-full-fit.json) obejmują zgodność
ze starszym fitem, rzeczywisty fit 10017 wierszy, reload i native score,
wpływ końcowych wierszy na medianę, uszkodzenie macierzy, odrzucenie złej
liczności oraz odmowę przy braku rezerw hosta. Dotychczasowe kontrakty
Pipeline/Threshold zachowują limit 10000; 13 wcześniejszych definicji schematu
modelu pozostaje identycznych. Pełny CI i chroniona publikacja są wymagane.

To wykonany komponent numeryczny. Jego wywołujący musi zweryfikować cały
publiczny parent i membership, zarezerwować rzeczywisty odczyt/fit w dzienniku
oraz zmierzyć całe przygotowanie cech. Powiązanie pełnego strumienia Point z
[membership i rolami treningowymi](ai09-native-anomaly-membership.md) jest wykonane.
Artefakt modelu Project i rzeczywisty dziennik pozostają do połączenia. Te kontrole nie są
fitem Project ani oceną świeżego final. Prawda offline, jakość, krytyczne grupy,
niepewność, komplet kosztów i lifecycle pozostają warunkami AI 09 ready.
