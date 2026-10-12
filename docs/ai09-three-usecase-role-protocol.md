# Wspólne role i rezerwacje trzech zastosowań

Kontrakt v50 dodaje `RoleAwareCampaignProtocol` i trwały dziennik do nowej,
prospektywnej deklaracji portfolio. Zachowuje dwanaście źródeł: trzy warianty
development oraz trzy warianty dla każdego seeda final 42/137/2026. Starsze
protokoły i rozpoczęte dzienniki zachowują dotychczasowy format oraz reguły.

Jedna lista pięciu okien określa rzeczywiste daty i granice dostępności
wyników dla wszystkich zastosowań. Walidacja uwzględnia dojrzałość prognoz
14 dni z faktycznym opóźnieniem etykiety zadeklarowanym przez Source, siedmiodniowe etykiety stockout oraz granicę
ściśle przed początkiem kolejnej roli. Okna muszą mieścić się w oryginalnej
historii development. Preprocessing pozostaje dopasowywany tylko na train.

Każda operacja deklaruje również dokładne `read_roles`:

| Operacja | Dozwolone role |
| --- | --- |
| Trening forecastingu | `train`, `early_stopping` |
| Trening anomaly i natywne progi pojemności | `train`, `tune` |
| Model bazowy stockout | `train` |
| Kalibrator forecastingu | `calibration` |
| Kalibrator stockout | `early_stopping` |
| Selekcja, ocena polityki, niezależna ocena | jedna jawnie wskazana rola operacji |
| Generowanie lub pełny odczyt rodzica Source | `all_parent_data` |

Wcześniejsza rola kalibracji stockout ma nowy, jawny format. Nie jest
przemianowaniem okna w starym dzienniku. Stary kontrakt nadal odrzuca
kalibrację w `early_stopping`.

Inventory stockout obejmuje dokładnie sześć baz: LR/HGB razy trzy warianty
cech, z seedem inicjalizacji 42 i zachowaną natywną polityką treningu.
Każda baza ma cztery zadeklarowane próby kalibracji C=0.01/0.1/1/10.
Każda operacja ma jedną próbę; nieudana lub niedokończona próba nie odzyskuje
budżetu i nie uruchamia automatycznego ponowienia.

`prerequisites` nadal wymaga sukcesu poprzednika. Nowe
`resolved_prerequisites` stosuje się wyłącznie do kalibratora stockout
i selekcji tych modeli. Pozwala zapisać rozstrzygnięcie próby po zakończonym
błędzie jej bazy. Nie pozwala zarezerwować jej przed zakończeniem bazy ani
zapisać udanego kalibratora, jeżeli baza zawiodła. Wywołujący musi wtedy
zapisać odmowę bez treningu, rzeczywisty koszt wykonanych kontroli i brakujące
koszty jako nieznane. Brak modelu nie jest udanym treningiem.

Selekcja wymaga rozstrzygnięcia wszystkich 30 prób. Ocena polityki stockout
wymaga zakończonej selekcji, a każda niezależna ocena wariantu wymaga tej
oceny polityki. Zachowane są oryginalne kontrole kosztów, kolejności,
pochodzenia, równych budżetów rodzin forecastingu, pełnego development
i zamrożenia wyboru przed final. Dziennik nie dowodzi jakości ani globalnej
świeżości final. Walidacja wartości Pythona poprzedza serializację i pierwszy
odczyt przy inicjalizacji, aby kopia obiektu nie mogła przemycić boola jako
liczbowego budżetu.

To odbiór deklaracji i mechanizmu dziennika. Przed uruchomieniem Project
trzeba połączyć tę wersję z aktualnym workflow 25/50/100, powiązać rzeczywiste
plany i fizyczne pliki ról z tymi datami, wdrożyć wykonanie wszystkich prób
oraz ocen stockout/anomaly i zamrozić cały runtime. Same `read_roles` nie
sprawdzają, które pliki otworzył zewnętrzny kontroler. Komponent nie zastępuje
tych kontroli ani niezależnego audytu danych, kosztów i wyników.

[09.120](evidence/09-120-three-usecase-role-protocol.json) zachowuje
**84 testy kodu i identyczne 84 z paczki**, w tym odmowy niewłaściwych ról,
niepełnego zestawu prób, nierozstrzygniętych zależności, ponowień i zmian
rozpoczętego dziennika. Test zamknięcia używa jawnych deklaracji, kontrolnych
hashy i kosztów testowych; nie uruchamia generatora ani modelu. 675 modułów
oraz 55 schematów mają zgodne bajty. AI 09 pozostaje **`not_ready`**;
canonical wyłączony, **12 GiB** i rezerwa systemowa **1 GiB**.
