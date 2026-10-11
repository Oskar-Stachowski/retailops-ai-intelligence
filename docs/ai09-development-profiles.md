# Profile development i budżet prób

Kontrakty [v29](../contracts/evaluation/v29/development_profile.schema.json)
dodają odrębne profile `ai09-development-25-v1` i `ai09-development-50-v1`.
Każdy zachowuje 365 dni od 2025-08-01 do 2026-07-31, seed danych 42,
pięć par sprzedaży, trzy lokalizacje zapasu oraz 14 dni znanych planów.
Górna liczba dziennych wierszy przed lifecycle wynosi odpowiednio 45 625
i 91 250. Source otrzymuje obsługiwany `ai-dev` z jawnymi nadpisaniami
rozmiaru. Odrębna tożsamość AI zawiera nazwę profilu i wszystkie parametry.
Żadne dane istniejącego rodzica nie są filtrowane ani skracane.

`prepare_development_profile` kompiluje trzy oryginalne plany generacji:
ordinary, demand i physical. Wymaga pełnych typów zaplanowanych anomalii
oraz kontrolnych okien w zachowanej historii. Oryginalny parser Source nadal
odpowiada za semantykę planu, poprawność ziaren i skutki interwencji.
Mały profil jest odrzucany przez dotychczasowe kontrakty canonical, również
po usunięciu nowego pola profilu. Pełny development 100 i końcowe portfolio
200 × 730 × 10 × 4 dla seedów 42/137/2026 pozostają bez zmian.

[Kontrakt wyszukiwania](../contracts/evaluation/v29/forecast_development_search.schema.json)
przygotowuje równy budżet RF/HGB/TensorFlow: dwie różne konfiguracje każdej
rodziny na 25 produktach, następnie po jednym finaliście na 50 i 100.
Łącznie to najwyżej 12 fitów, po jednej próbie każdej zadeklarowanej pozycji,
z osobnym seedem inicjalizacji 42. Rodziny mają wspólny limit czasu, RAM,
scratch i CPU w danej skali; środowisko TF ma własny przypięty lock.
Limit pojedynczego fitu wynosi najwyżej 1200 s, RAM najwyżej 12 GiB,
scratch najwyżej 8 GiB, z rezerwami 1 GiB RAM i 6 GiB dysku.

Reguła przesiewu deklaruje MSE średniej na Tune osobno dla rodziny, z istniejącą
bramką bias i coverage oraz kolejnością receptur do rozstrzygania remisów.
Żadna rodzina nie znika tylko dlatego, że przegrała z inną. Brak poprawnej
próby którejkolwiek rodziny ma zatrzymać awans. Końcowy wybór funkcji prognozy,
kalibracja i pełne bramki trzech zastosowań pozostają natywne.
Wszystkie nieudane próby i ich koszty mają pozostać w dzienniku.

`compile_forecast_search_fits` sprawdza tożsamość właściwego przygotowania
25/50 albo oryginalnego pełnego protokołu i tworzy istniejące
`CampaignForecastFitPlan`. Zachowuje wszystkie kwalifikujące się klucze,
parametry i seed, wspólny budżet oraz osobne środowiska. `freeze_forecast_search`
zapisuje niezmienną deklarację 0600 z fsync i odmawia nadpisania.

**Zakres v29:** to kontrakty i kompilator planów. Osobny
[wykonawca przygotowania v30](ai09-development-preparation.md) dodaje trwały
dziennik trzech generacji oraz oryginalne sześć faz. Automatyczny wybór
finalisty nadal nie jest wykonany. Nazwy finalistów podane kompilatorowi
nie są dowodem zwycięstwa. Potrzebne pozostają: rzeczywiste przygotowanie
małych profili, wykonawca prób i weryfikator metryk wyboru, powiązanie tej samej
decyzji z krokami 50/100 oraz rzeczywiste pokrycie krytycznych grup.
Deklaracja nie daje uprawnienia do treningu, otwarcia final ani promocji.
Konkretny protokół Project nie został jeszcze zamrożony ani uruchomiony.

[Dowód 09.86](evidence/09-86-development-profile-design.json) obejmuje
kontrole kontraktów, kompilatora i trwałego zapisu oraz oryginalny parser
przypiętego Source. Ten parser potwierdził zgodność sześciu konfiguracji
i kontraktów scenariuszy z kontrolnymi IDs; nie generowano pełnych danych
ani nie sprawdzano efektów na rzeczywistych ziarnach. Profile wymagają
natywnego wykonania i pomiaru, a przyrost — pełnego CI i publikacji na main.
