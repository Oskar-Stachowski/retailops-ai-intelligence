# Trening stockout na całej populacji AI 09

Nowe moduły `campaign_stockout_training` i `campaign_stockout_calibration`
przyjmują całą, uporządkowaną populację do miliona wierszy. Limit jest granicą
kontraktu, a nie dowodem zmierzonej pojemności. Wykonane kontrole obejmują
10 001 wierszy, przekraczając dotychczasowy limit 10 000. Moduły i kontrakty
zamkniętego AI 08 pozostają niezmienione.

Trening zachowuje LR/HGB, warianty cech, hiperparametry, brak ważenia klas,
naturalną częstość zdarzeń oraz preprocessing wyuczony wyłącznie na TRAIN.
Wszystkie wiersze trafiają do jednej macierzy treningowej; małe partie ograniczają
pamięć konwersji i weryfikacji predykcji. Nie są próbką ani osobnymi modelami.
Imputacja, skalowanie i słowniki kategorii obejmują cały TRAIN.

Osobny kalibrator wykorzystuje późniejszy okres, dodatnie nachylenie surowego
score oraz natywne offsety kategorii, lokalizacji i historycznego ograniczenia
zapasu. Zachowuje wcześniejszą siatkę C: 0.01, 0.1, 1.0, 10.0.
Konkretna próba i wybór konfiguracji muszą należeć do zamrożonego programu.
Nieznane późniejsze kategorie korzystają z zerowych offsetów; predykcja
nie aktualizuje słowników ani parametrów. Model i kalibrator mają osobne
czasy dostępności. Zbyt mała liczba obu klas albo niedodatnie nachylenie
uniemożliwiają przyjęcie kalibratora.

Stan modeli jest przenośnym JSON-em. Każdy fit porównuje wszystkie predykcje
przenośnego modelu z estymatorem scikit-learn w tolerancji `rtol=1e-10`,
`atol=1e-12`. Hashe kluczy i etykiet mają identyczną definicję jak w AI 08,
ale powstają przyrostowo, bez dodatkowej pełnej listy. Nowe wersje v42
oddzielają większą populację od wcześniejszych formatów i ich limitów.

**29 testów źródeł i te same 29 z zainstalowanej paczki przeszło bez pominięć**,
w tym sześć porównań rodzina/wariant z AI 08,
cztery wartości C, rzeczywiste fity LR/HGB i kalibratora na 10 001 wierszach,
odczyt JSON, predykcja całej populacji, odmowy po zmianie chronologii i kluczy
oraz zachowanie parametrów przy późniejszych brakach i nowych kategoriach.
Różne zapisy tego samego czasu UTC nie pozwalają podwójnie policzyć klucza.
Mypy sprawdził 790 plików; cztery nowe schematy nie zmieniają starszych wersji.
[Dowód 09.107](evidence/09-107-native-stockout-full-training.json) zawiera zgodność
657 modułów i 35 plików kontraktów w źródłach, paczce i instalacji. Publikacja
oraz pełny Required CI dokładnego PR i wynikowego main pozostają wymagane.

To komponent obliczeniowy. Nie otwiera Source, nie weryfikuje dojrzałości etykiet
ani pełnego membershipu i nie rezerwuje operacji w dzienniku. Adapter kampanii
musi zapewnić te dowody przed fitami, przypiąć polityki i progi, rozliczyć każdą
próbę oraz zmierzyć zasoby. Ocena ordinary/demand/physical i final dla trzech
seedów, niepewność, segmenty, MLflow i lifecycle nadal są wymagane.
Kontrole na sztucznych danych nie są kwalifikacją modeli Project ani `ready` AI 09.
