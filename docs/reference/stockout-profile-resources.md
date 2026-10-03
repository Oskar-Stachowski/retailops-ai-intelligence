# Większy profil stockout — ograniczenia i projekt wykonania

To projekt kolejnego przyrostu, **nie wykonany odbiór większego profilu**.
Nie wygenerowano nowych danych i nie podniesiono istniejących limitów.
Obowiązują wymiary z
[planu źródła](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/08639e9188badb352ed64686a088fe237badad41/docs/plans/ai/etapy/08-stockout-risk.md)
i [kontraktu profili](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/08639e9188badb352ed64686a088fe237badad41/docs/plans/ai/kontrakty/profile-i-bramki.md)
w przypiętej rewizji producenta `08639e9`.

## Pomiary i szacunek

Odebrane temporal smoke ma 8 produktów, 2 fizyczne lokalizacje i 102 dni:
1632 bazowych punktów, nie 2448 wierszy selling location/channel.
Plik cech ma **10 249 881 bajtów**, upstream 2 127 982, comparison
1 407 074, etykiety 666 623, split 483 435 i modele 864 342.
W JSON każdego punktu powtarzają się 28 dni historii i pełne lineage.

| Profil | Górna liczba fizycznych origin przed eligibility | Cechy JSON przy obecnych bajtach/punkt |
|---|---:|---:|
| Odebrany temporal smoke | 1 632 | 9,78 MiB, pomiar |
| Przykładowe 32 produkty × 2 lokalizacje × 102 dni | 6 528 | 39,10 MiB, szacunek |
| `ai-dev`: 100 × 3 × 365 | 109 500 | około 656 MiB, szacunek |
| `ai-training`: 200 × 4 × 730 | 584 000 | około 3,42 GiB, szacunek |

To liniowy szacunek pliku cech, nie pamięci procesu, czasu ani sumy danych
generatora. Lifecycle i eligibility zmniejszają rzeczywiste liczby.
Transakcje/ledger mają inne mnożniki; nie oszacowano ich z liczby origin.
Rozkłady, liczba kategorii i długość historii też mogą zmienić bytes/point.

Obecne ograniczenia blokują prostą większą generację:

- `stockout.dataset.INPUT_LIMITS`: 500 000 wierszy i 64 MiB dla wejścia
  kwalifikowanego przez ścieżkę etykiet/cech; to limit konsumenta stockout,
  nie ogólne stwierdzenie o maksymalnej wielkości wszystkich snapshotów.
- Label policy: 10 000 okien oraz 100 000 wierszy ledgeru;
  etykiety i split używają limitu JSON metadata 4 MiB.
- Cechy: 10 000 punktów i 16 MiB. Upstream/comparison i modele mają również
  ograniczony JSON 16 MiB; trening przyjmuje do 10 000 wierszy.
- Pełne tabele faktów są ładowane do list. `feature_point` przegląda
  tabele dla każdego origin, a każda z 28 dat ponownie wybiera wersje
  popytu/routingu i przegląda ledger. Koszt nie musi rosnąć liniowo.

Sześć fitów modeli/kalibratorów z metrykami zajęło 0,466 s na smoke.
Pełne CLI około 99 s głównie odtwarza rodziców; nie jest prognozą czasu
większego datasetu. Najpierw trzeba zmienić przygotowanie danych.

## Projekt następnego przyrostu

1. Zachować obecny czytnik i artefakty jako wersję 1.0. Dodać odrębny
   manifest partycji z nowym schema/policy/implementation ID. Manifest
   wiąże rozłączne klucze, wymiary profilu, rodziców i checksums każdej
   partycji; verify wymaga wszystkich partycji bez braków i powtórzeń.
2. Zapis Parquet dzielić po fizycznej serii i czasie, początkowo batch
   256 origin, najwyżej 16 MiB na partycję. Historyczne wiersze i lineage
   przechowywać osobno z referencjami, bez kopiowania całych struktur dla
   każdego punktu. Jawnie weryfikować availability referencji względem origin.
3. Zbudować indeksy po product/stock/date oraz wersjach znanych w origin.
   Ledger liczyć kumulacyjnie; ostatnie 28 dni sprzedaży/coverage utrzymywać
   w ruchomym oknie. Zachować selekcję latest tylko spośród dostępnych
   wersji — globalna najnowsza wersja nie może przepisać historii.
4. Rodziców sprawdzać raz na spójny przebieg. Zweryfikowane referencje
   pozostają przypięte do checksums; zmiana pliku lub niekompletna partycja
   blokuje wynik. Trening pobiera wyłącznie dojrzałe role development,
   a final test nadal nie udostępnia outcomes do doboru modelu.
5. Najpierw porównać nową projekcję z każdym z 1632 starych punktów,
   licznikami i wynikami modeli. Odbiór obejmuje late correction,
   future plan, staleness, overlap splitu i restart częściowego zapisu.
   Sprawdzić brakujące, uszkodzone lub powtórzone partycje oraz powtarzalny replay.
6. Dopiero po zgodności wykonać większy development z prawdziwą nazwą
   profilu. Jeśli potrzeba próby pośredniej, dodać jawnie nowy profil
   producenta; nie nazywać 32 produktów pełnym `ai-training`.

## Proponowany budżet pilota

Przed uruchomieniem nowego większego **pilota**, zapisać w wersjonowanej
konfiguracji: maksymalnie 5 GiB nowego scratch, 1 GiB peak RSS całego drzewa
procesów i 10 minut na przygotowanie. Wymagać co najmniej 35 GiB wolnego
miejsca przed startem, co zachowuje 30 GiB rezerwy przy pełnym zużyciu scratch.
Limity dotyczą pilota, nie stanowią deklaracji wykonalności `ai-dev` ani
`ai-training`. Jeśli oszacowane wejście przekracza budżet, nie zaczynać runu.
Monitorować i przerwać własny izolowany proces przy przekroczeniu;
nie usuwać danych innej sesji w celu kontynuowania.

Raport musi mierzyć wall/CPU, jednoczesne peak RSS drzewa, bytes i liczbę
plików wejścia/wyjścia, rows/s, największą partycję oraz wolny dysk
przed/po. Importy i replay rodziców są częścią całego pipeline; sam fit
ma osobny timer. Brak pomiaru to `not_ready`, nie zaliczenie zasobów.
Pełny profil dostanie osobny budżet na podstawie pilota, przed generacją.

Większa próba ma rozliczyć obie klasy i liczebności kategorii/lokalizacji,
bez przyjmowania z góry, że więcej produktów automatycznie zaliczy bramki.
Scenariusze/seedy, split, metryki i końcowe progi odbioru należy zamrozić
przed final testem. Ten dokument nie zatwierdza promocji ani progów serving.
