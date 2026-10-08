# Pełna projekcja dni anomaly w AI 09

`CampaignAnomalyDayProjection` indeksuje wszystkie publiczne wiersze pięciu
tabel kwalifikacji dnia, po pełnym odbiorze snapshotu i curated przez
[publiczny parent](ai09-native-anomaly-parent.md). Każda tabela przechodzi
niezależny natywny Digest. Końcowe kontrole rodzica i runtime obejmują cały
Source, także tabele spoza projekcji.

Projekcja działa osobno dla każdej pełnej serii produkt–lokalizacja–kanał–waluta.
W serii zachowuje wszystkie sprzedaże, obserwacje i reklamacje oraz właściwy
produkt i politykę reklamacji. Używa oryginalnego `declarations`; zachowuje
znane chwile zamknięcia, źródłową kompletność, brakujące i zamknięte dni,
wszystkie wymagane identyfikatory sprzedaży i pełny okres reklamacji zakupów
z parenta. Nie usuwa późnych dni ani nie zmniejsza populacji przy wyczerpaniu
budżetu. Globalna liczba dni musi dokładnie odpowiadać zamrożonemu planowi.

Natywne limity liczby dni jednej serii i identyfikatorów w Day pozostają
w mocy. Projektowy indeks ma odrębne limity całej populacji, liczby serii,
rekordu, pełnej serii i przydzielonych oraz brudnych stron SQLite. Budżet
obejmuje także rollback journal i pomocniczy Digest. Indeksy pokrywają
kolejność odczytu; pomocniczy Digest nie wymaga nieśledzonego sortowania.
Po zweryfikowaniu wszystkich dni tymczasowe indeksy wejściowe są usuwane;
SQLite zwalnia ich strony bez tworzenia drugiej kopii bazy. Ponowny pełny
hash sprawdza, że zwolnienie danych pomocniczych zachowało każdy dzień.
Budżet jest sprawdzany także przed commitami, gdy istnieją brudne strony
i rollback journal. Receipt zapisuje największy zaobserwowany indeks oraz
rozmiar zachowanego indeksu dni.

Audyt rzeczywistego publicznego fixture ordinary wykrył błąd w natywnym
`declarations`: odczyt pustej serii z defaultdict tworzył pustą grupę zakupów,
po czym wyznaczanie ostatniej sprzedaży kończyło się błędem. Odczyt `get`
zachowuje deklarację dnia bez sprzedaży i nie tworzy kohorty reklamacji.
Przypadki open, closed i missing zachowują odpowiednio znane zero,
zamkniętą lokalizację i nieznaną wartość, dopiero po dostępności deklaracji.
Ta poprawka nie zmienia obliczeń istniejących grup zakupów ani kontraktów
i limitów AI 07.

Read-only Mapping udostępnia dni przez pełny klucz GRAIN. Brak deklaracji
pozostaje brakiem, a nie zerem. Odczyty sprawdzają hashe i klucze; kontrola
końcowa ponownie liczy całą populację i jej hash. Zmieniony, ponownie
zapieczętowany, przemianowany lub usunięty dzień uniemożliwia odbiór.
Indeks działa wewnątrz jednego kontekstu parenta i usuwa własny scratch.

[Dowód 09.60](evidence/09-60-native-full-day-projection.json) zachowuje pierwsze
22 błędy i 15 błędów przygotowania oraz ich rzeczywiste przyczyny. Po poprawkach
96 kontroli, obejmujących dotychczasową kwalifikację dnia, przeszło bez pominięć.
Końcową optymalizację retencji sprawdziło 48 kontroli oraz 48 kontroli
z zainstalowanej paczki. Pełne CI i publikacja pozostają wymagane.

Receipt potwierdza pełną projekcję deklaracji. Wymaga również poprawnego
zakończenia zewnętrznego kontekstu publicznego parenta. Nie zastępuje dowodu
genealogii generation, polityki zamknięcia producenta, autoryzowanego odczytu
w dzienniku ani kwalifikacji raw-DQ w historycznej chwili. Kolejne wymagane
części to bramka dnia na dysku, globalna nieprzypisana kwarantanna, causal
Point census, truth ordinary/demand/physical i ich rzeczywiste powiązanie
z kampanią. Jakość modeli oraz pełny etap pozostają niezakwalifikowane.
