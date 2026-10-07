# AI 08.1 — lokalny odbiór etykiet nowych braków towaru

Pierwszy zakres implementuje etykiety `incident_stockout_7d` dla fizycznej
pary produkt–magazyn. [Kontrakt i polecenia](../reference/stockout-labels.md)
opisują granice czasu, niepełne okna i prywatny handoff AI 06.
[Wersjonowany pomiar](08-01-stockout-labels.json) przypina kod, wejścia,
wynik oraz obie próby wykonania. Bazą gałęzi jest main
`f4ae14fe6588b91506383a709b5a0185208a4ea6`, po scaleniu AI 05.

## Rzeczywista próba małego handoff

Użyto istniejącego `data/fixtures/inventory-v1_1.zip`: 223 pliki,
1 581 853 bajty po rozpakowaniu obu wariantów. Prywatny snapshot ma
108 ruchów magazynowych i 60 dobowych okien. Nie generowano nowych danych
treningowych ani nie czytano operacyjnej bazy RetailOps.

| Wynik dla 60 okien | Liczba |
|---|---:|
| Nowy brak w dojrzałym siedmiodniowym oknie | 3 |
| Brak nowego braku w pełnym dojrzałym oknie | 9 |
| Brak istniejący już na początku | 13 |
| Niepełny ogon historii, bez etykiety | 35 |

Kod w repozytorium wykonał build/rebuild/verify w 1,147 / 0,669 / 0,755 s.
Wheel zainstalowany bez zależności do osobnego katalogu, uruchomiony poza
repozytorium, wykonał je w 0,664 / 0,558 / 0,575 s. Obie próby dały
identyczny identyfikator, hash i bajty artefaktu. Wszystkie importowane moduły
konsumenta w drugiej próbie pochodziły z zainstalowanego pakietu; pakiet
producenta `data` nie był dostępny. Szczyt RSS drzewa procesu wyniósł
82 526 208 bajtów (około 78,7 MiB), próbkowany co 10 ms.

Ponowny build zwrócił `reused`; pełny verify odtworzył wynik. Plik ma tryb
`0600`. Hashe wszystkich 223 plików wejścia przed i po próbach są identyczne.
Nie uruchamiano usług, treningu, aktualizacji registry lub promocji modelu.

## Kontrole

Regresja etykiet, importer inventory i curated przeszła **73/73 testów**,
w tym 36 nowych przypadków. Obejmują dokładne granice czasu, chwilowe zero,
opóźnione fakty, stare/nieznane zapasy, brak coverage, fizyczny grain,
nieprawidłowy ledger oraz odmowę przy etykiecie zmienionej i ponownie
zahashowanej. Kontrola typów całego źródła i lokalne kontrole Ruff przeszły.
Zainstalowany CLI odrzucił też publiczny snapshot oraz brak jawnej zgody na
evaluation truth; obie próby zakończyły się kodem 1 bez utworzenia wyniku.

Zestaw bramek `make ci-local` został ukończony poza sandboxem:
**1766/1766 testów w 1535,11 s**, Ruff, mypy, docs, runtime, handoff,
snapshot/import, curated, forecasting, kontrakty, sdist/wheel, Compose config
i dwa skany sekretów. Pierwsza próba była ograniczona przez sandbox;
powtórzona zakończyła testy i kontrolę pakietu. Po poprawieniu lokalnej
ścieżki do Dockera dokończono pozostałe targety, bez powtarzania zaliczonych.
Build ze sdist dał ten sam hash wheel co próba instalacyjna.
Skan historii i plików nie wykrył sekretów. Testy zgłosiły jedno ostrzeżenie
joblib o wykrywaniu liczby fizycznych rdzeni, bez niezaliczonych testów.
Zdalny Required CI pozostaje do odebrania. Aktualny stan podaje
[status](../STATUS.md).

## Granice odbioru

To odbiór małego fixture i pierwszej warstwy etykiet. Kwalifikację lifecycle,
assortment, routing i sales coverage dostarcza zweryfikowany producent AI 06;
AI 08 niezależnie przelicza wynik z ledgeru oraz kontroluje dostępność,
coverage i zgodność z tym handoff. Nie przeprowadzono pełnej niezależnej
ponownej kwalifikacji wszystkich okien producenta.

Nie ma jeszcze features, temporalnych splitów, treningu, kalibracji ani
oceny jakości modelu. **Cały AI 08 nie jest ready**. Następny zakres to
budowa cech znanych w cutoff i przygotowanie czasowego podziału danych.
