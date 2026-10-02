# AI 04 — sprzedaż przerywana i korekta 2.1

Status: pierwszą próbę pełnej kampanii zatrzymał brak miejsca na dysku przed
ukończeniem curated. Nowe holdouty nie zostały ocenione. Ten dokument nie
kwalifikuje modelu ani nie zamyka punktu 3. Progi `QualityPolicy` pozostają
identyczne z pierwotnym kontraktem.

## Przyczyna braku zero i scenariusz źródła

`zero` oznacza średnią równą zero w historii 28 dni znanej w origin.
Dotychczasowy `ai-dev` ma bazową intensywność `long_tail` 0,6 pomnożoną przez
wagę produktu, ruch, kalendarz, ceny i losowy czynnik. Niska sprzedaż dzienna
nie zapewnia 28 kolejnych dni bez sprzedaży. V8 wykazała brak takich historii
w trzech wymaganych oknach, jeszcze przed etykietami target i treningiem.

Nowy jawny profil `ai-intermittent-v1` zachowuje seed, losowania katalogu i
lokalizacji oraz pozostałe klasy popytu. Dla `long_tail` zmienia wyłącznie
bazową intensywność na 0,06. Zdarzenia dodatnie nadal wynikają ze zwykłego
stochastycznego zaokrąglania. Fakty przechodzą normalną ścieżkę koszyków,
inventory, kompletnego panelu i historii dostępności. Nie dopisujemy zer ani
kopii wierszy; braki i nieaktywne okresy zachowują dotychczasowe znaczenie.

Kod scenariusza: repozytorium platformy, branch `ai/04-intermittent-source-v1`,
commit `1d49c1afe2fab4840dbd00522d3553b03c5165a7`. Parametry: seed 42,
162 dni do 30 września 2026, 100 produktów, 2 sklepy i 2 magazyny.
Scenariusz nie korzysta z okien prognozy ani wyników modelu. Jest testem
syntetycznym, a nie dowodem reprezentatywności danych produkcyjnych.

## Korekta prognozy

Receptura 2.1 dopasowuje korektę średniej w pierwszej połowie validation.
W drugiej wybiera spośród oryginalnych i skorygowanych predykcji oraz
zamrożonych mieszanek z baseline'em (wagi 0,25 / 0,5 / 0,75 / 1).
Kandydat musi spełniać ograniczenia bias i regresji MAE w obu blokach.
Zmiana wymaga poprawy MAE >5% w drugim bloku albo naprawy naruszonego bias
baseline'u. Brak kandydata zachowuje baseline i jawne niezaliczone bramki.
Warunek globalnej poprawy MAE oraz wszystkie pozostałe bramki pozostają
niezmienione i są ponownie oceniane dla całego wyniku.

Przedziały 2.1 zachowują signed residual equal-tail nominal 90% z drugiego
bloku validation oraz poprzednie reguły minimum próbki i fallbacku.
Wersja 2.0 eksperymentalnie używała najkrótszego przedziału reszt skalowanych;
nie została przyjęta z powodu gorszego pokrycia na starych danych rozwojowych.
Jej kod i konfiguracja pozostają w commicie `a1bfdb6`, wynik jest zachowany.

| Receptura, te same stare dane | Passed | Failed | Not ready | Regresja MAE low, pooled holdout |
|---|---:|---:|---:|---:|
| Pierwotna ocena | 145 | 79 | 8 | wynik w poprzednim evidence |
| Korekta 1.0 | 192 | 32 | 8 | +13,21% |
| Odrzucona 2.0 | 186 | 38 | 8 | +9,84% |
| Korekta 2.1 | 195 | 29 | 8 | +9,84% |

2.1 ma globalny MAE o 12,25% niższy od zamrożonego baseline'u. Pooled low
przechodzi bramki: coverage 89,13%, bias -1,44%, width/mean 1,004. Pooled
medium i high również przechodzą. Poszczególne foldy i kategorie nadal
mają błędy: bias 12 wskazań, width 16, regresja 9, coverage 6; przyczyny mogą
współwystępować w jednej z 29 niezaliczonych bramek. Osiem `not_ready`
pozostaje na starym źródle bez segmentu zero.

Wynik 2.1: `forecast-remediation-sha256-3296a396085c6a1eaf98ece8f24b02e2e8e0dc1ca3bf0cdeeb199db8c7ab05da`.
To diagnostyka na wcześniej obserwowanym development, nie niezależny test.

## Zapis pełnej próbki i limity zasobów

Stary zestaw dla jednego sklepu ma 1952151135 bajtów rozwiniętych historii i
cech. Dwa sklepy wymagają około 3904302270 bajtów, ponad dotychczasowym limitem
2 GiB. To głównie wielokrotnie powtarzana metadokumentacja pochodzenia cech.
Implementacja wejść 1.1 kompresuje każdy rekord tymczasowego indeksu sortowania
SQLite za pomocą zlib. Kanoniczna treść, kolejność do hashowania, cechy,
liczba wierszy i populacja pozostają takie same.

Jawny limit rozwiniętej treści wynosi teraz 4 GiB. Limit fizycznych plików
pozostaje 2 GiB, a tymczasowy indeks ma dodatkową kontrolę 2 GiB. Limit rekordu
1 MiB, bufora 16 MiB, batcha 64 MiB, maksymalna liczba wierszy i limity modeli
pozostają bez zmian. Kampania przypina implementację i te limity przed oceną.
Testy sprawdzają równoważność kanonicznej treści na wierszach w odwróconej
kolejności, wykrywanie duplikatów i odrzucanie przekroczeń obu budżetów.

## Zamrożona ocena na nowych danych

[Kampania 10](../../contracts/forecast/v1/quality-remediation.campaign-v10.json)
przypina źródło, recepturę 2.1, hash kodu i kompletną politykę backtestu.
Commit zamrożenia receptury: `509aa94`; przypięcie tożsamości źródła:
`90dc2be`; przypięcie implementacji i limitów zapisu wejść: `c9bb8ef`.
Jej holdouty: 29 lipca–11 sierpnia,
12–25 sierpnia i 26 sierpnia–8 września 2026. W chwili zamrożenia wyniki tych
holdoutów nie były oceniane. Najpierw wymaga konserwatywnej kontroli historii,
następnie pełnej kontroli cech, co najmniej 30 wierszy każdego wymaganego
koszyka w każdym oknie. Same liczebności nie dowodzą jakości predykcji.

Źródło `source-sha256-76994412fcabec320688429d7f88adcd80fe54fb70327b22c1456c27673c2d66`
zostało wygenerowane i zweryfikowane. Eksport
`snapshot-sha256-ffe024c05fe2400d904cd7753e550a4ff900f7e68fdc86f0cbba8ecdb54e12f7`
obejmuje 616547 wierszy w 43 tabelach, bez evaluation truth. Import do AI
potwierdził zgodność typowanej treści kanonicznej. Pierwsza próba zakończyła
się `sqlite3.OperationalError: database or disk is full` podczas weryfikacji
kopii snapshotu dla curated. Zachowano raport i log próby 1. Nie wykonano
nowych dopasowań modeli ani oceny metryk nowych holdoutów.

Diagnostyka surowej historii dostępnej w origin znalazła przypadki `zero`
w każdym z sześciu okien: odpowiednio 134/161, 109/149 i 155/101 par
origin–series dla validation/holdout kolejnych foldów. To kontrola obecności
historii, bez pełnej kwalifikacji cech i bez oceny targetów. Formalne kontrole
source preflight oraz feature preflight pozostają do wykonania.

## Weryfikacja kodu

Pełna regresja AI przed zmianą zapisu wejść: 949 testów zaliczonych. Po tej
zmianie: 66 testów ukierunkowanych na cechy, manifesty, budżety zapisu i
recepturę 2.1. Niezależne ponowne przeliczenie starego wyniku 2.1 potwierdziło
identyczny artefakt. Ruff, formatowanie, mypy, kontrakty, dokumentacja i
kontrole komponentów przeszły. Zbudowano wheel i sdist; izolowana kontrola
wheela potwierdziła dostępność modułu 2.1 i obu schematów.

W repozytorium źródła pierwsza regresja miała 821 zaliczonych testów i dwie
odmowy dostępu do Dockera w sandboxie. Oba przypadki zaliczyły ponowny bieg
poza sandboxem, razem z testami nowego profilu i CLI (8 zaliczonych testów).
Kontrole Gitleaks obejmujące nowe commity obu repozytoriów przeszły.

[Zapis maszynowy](04-quality-intermittent.json) zawiera konfigurację, raporty,
tożsamości artefaktów, sumy kontrolne i wyniki regresji.

Po ocenie tej kampanii nie wybieramy ponownie metody na jej holdoutach.
Portfolio final test pozostaje poza zakresem. Brak operacji AWS, registry,
promocji i serving. Dopuszczenie modelu wymaga osobnego odbioru AI 05.
