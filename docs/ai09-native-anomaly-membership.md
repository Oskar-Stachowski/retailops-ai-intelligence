# Pełny podział danych anomaly i trening

Pełne historyczne Point mają teraz połączenie z natywnym podziałem na train,
validation, odstępy między oknami i test. Plan development przypina hash planu
cech, całego strumienia Point, wszystkie serie, okna i chwile odcięcia wiedzy.
Odczyt żywego parenta odrzuca plan pomijający dowolną rzeczywistą serię.
Każdy zadeklarowany dzień pozostaje w liczniku, także przy braku danych,
niedostatecznej historii lub zbyt późnej dostępności wyniku.

Adapter wykorzystuje niezmienione funkcje `requested` i `count_rate_row`.
W pamięci przechowuje jedną pełną serię i sześć poprzednich dni. Wektory
powstają wyłącznie dla uprawnionych wierszy train i validation. Odstępy,
test i wiersze wykluczone przez cutoff nie trafiają do treningu ani progów.
Zmiana późniejszych wartości nie zmienia wierszy treningowych.

`fit_anomaly_membership_census` najpierw wyczerpuje cały podział danych.
Sprawdza kolejność, każdą serię i datę, role, zegary, wykluczenia i zgodność
wektora z membership. Dopiero potem uruchamia rzeczywisty fit. Dodatkowy,
brakujący lub niepoprawny końcowy wiersz blokuje wszystkie treningi.
Hashe train i validation obejmują także wykluczone i nieznane membership;
są identyczne z hashami pełnych tablic JSON dotychczasowego algorytmu.

Wektory są zapisywane w maksymalnie ośmiu prywatnych plikach: dwie role
razy natywne grupy zdarzenie/waluta. Obowiązuje pełna liczność każdej grupy,
do miliona uprawnionych wierszy na rolę, domyślnie 2 GiB łącznego zapisu,
limit jednego wiersza i 6 GiB rezerwy dysku. Odczyt sprawdza niezależnie
liczność, bajty, kodowanie, typ oraz hash całego pliku; dowiązania są odrzucane.
Wszystkie pliki tymczasowe są usuwane po sukcesie i po błędzie.

Trening wykorzystuje każdy uprawniony wiersz train i pełne mediany.
Progi obu rodzin wykorzystują wszystkie uprawnione wiersze validation,
z oryginalnymi limitami alertów osobno dla sprzedaży i zwrotów.
Scoring lasu działa w porcjach do 8192 wierszy. Maksymalnie 64 próby
zgodności na grupę nie ograniczają populacji treningowej ani progów.
Grupy bez danych zachowują zero wierszy oraz brak modelu i progu.

[Dowód 09.69](evidence/09-69-native-anomaly-membership-training.json) obejmuje
zgodność ze starszym fitem i progami, pełny trening 10020 wierszy,
publiczne warianty ordinary/demand/physical oraz odmowy przy uszkodzeniu
pliku, złej liczności, cutoff, roli i braku rezerwy. Jest to kontrola
komponentów; pełny CI i chroniona publikacja pozostają wymagane.

Wynik zawiera grupy numeryczne, hashe membership, pełne liczniki oraz koszty
poszczególnych fitów. Wywołujący nadal musi zamknąć wszystkie konteksty
publicznego parenta, powiązać i zapisać model Project oraz rozliczyć całą
operację w rzeczywistym dzienniku, także przygotowanie i nieudane próby.
Nie uruchomiono fitów Project ani świeżych odczytów final. Integracja prawdy
offline, kwalifikacja jakości i pełny odbiór AI 09 pozostają otwarte.
