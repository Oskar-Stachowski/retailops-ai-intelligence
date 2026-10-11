# Publiczne dane stockout AI 09

Adapter `CampaignStockoutPublicParent` odtwarza cały publiczny snapshot i jego
transformację do curated. Następnie przechodzi przez wszystkie klucze
produkt–miejsce zapasu–czas migawki. Nie wybiera dogodnych produktów, dat ani
wierszy. Limity dotyczą całego odczytu; ich przekroczenie kończy operację błędem.

Cechy wylicza dotychczasowa funkcja AI 08, z faktów dostępnych w danej chwili.
Indeks przechowuje wszystkie wersje na dysku, a w pamięci najwyżej jedną serię
fizyczną. Ten sam mechanizm nadal obsługuje kontekst forecastingu. Stockout ma
własną tożsamość źródła i nie potrzebuje pozornego identyfikatora eksportu forecast.
Kategoria pochodzi wyłącznie z katalogu znanego w chwili obserwacji.

Strumień zachowuje również `already_stockout` i `insufficient_data`, powody
niekwalifikowania, historię oraz pochodzenie cech. Odbiór zawiera liczbę wszystkich
kluczy, rozkład statusów, hashe cech oraz dowody pełnego odtworzenia rodziców.
Jest dostępny dopiero po wyczerpaniu strumienia i pomyślnym zamknięciu kontekstu.
Przerwanie odczytu albo zmiana wejścia, także niewykorzystanej tabeli, uniemożliwia
przyjęcie odbioru. Rodzic i strumień są jednorazowe.

Nowe schematy v43 opisują plan i tożsamość stockout. Kontrakty oraz kod AI 08
pozostają niezmienione. **63 testy źródeł przeszły w 390,10 s**, bez pominięć.
Kontrola obejmuje istniejące publiczne fixtures 1.1 i 1.2 oraz regresję kontekstu
forecastingu. Mypy sprawdził 792 pliki. [09.108](evidence/09-108-stockout-public-parent.json)
zachowuje wynik i pomiary. Te same **63 testy z paczki przeszły w 395,98 s**.
659 modułów i 37 plików kontraktów mają identyczne bajty w źródłach, paczce
i instalacji. Odbiór dokładnego PR oraz wynikowego main jest nadal wymagany.

To przygotowanie publicznych cech. Kalibracja i trening na całej populacji są
opisane [osobno](ai09-native-stockout-training.md). Wciąż trzeba podłączyć
retrospektywny upstream, dojrzałe etykiety i pełny podział czasowy, rezerwacje
operacji, wybór modelu, progi oraz ocenę trzech wariantów i końcowych seedów.
Sam adapter nie odczytuje prywatnych etykiet, nie uprawnia do fitu ani final test
i nie oznacza `ready` AI 09.
