# Późniejsze źródła do końcowej oceny stockout

Wybrany model pochodzi z `stockout-later-calibration-selection-2.0.0`, seed 42,
ze znajomością wyboru od 2026-07-13. Przygotowanie opisane tutaj nie zmienia
modelu ani kalibratora. Nie jest oceną końcową lub zgodą na promocję.

## Prospektywny profil

Producent jest przypięty do `61eb215193106cc3f41e6b79e0470585cc9e791b`
z [draft PR #94](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/94).
Profile 1.8 mają 30 produktów, 3 lokalizacje sprzedaży, 2 fizyczne magazyny,
102 dni 2026-06-09–2026-09-18 i seedy 42/137/2026. `ai-stockout-stress-v1`
ma deterministyczne członkostwo szoku: SHA256 produktu modulo 3 daje zero.
W dniach 2026-07-28–2026-08-03 mnoży źródłowy popyt przez 2 przed budową
koszyków, realizacją i ledgerem. Domyślne zapasy i dostawy pozostają częścią
przypiętej fizyki producenta. Ostatnie dni pozwalają obserwować skutki.
Wcześniejsze naturalne promocje są teraz w późniejszym okresie oceny.

To odrębne syntetyczne światy. Tych samych SKU/magazynów/dat z różnych światów
nie wolno przedstawiać jako niezależnych duplikatów lub jednej ciągłej historii.
Prywatny czynnik scenariusza służy symulacji i diagnostyce oceny; nie jest cechą
wejściową runtime. Wymagane są rzeczywiste wsparcie, okna kontrolne i overlap
każdego scenariusza; sama nazwa profilu ich nie zalicza.

## Przygotowanie i granice

`scripts/prepare_stockout_final_sources.py` i osobny workflow mają tylko odczyt
GitHuba. Producent działa w oddzielnym procesie i klonie. Konsument jest przypięty
do `4faaf4b6c1997fda3a609645595165643bf302a9`. Import publiczny/prywatny,
curated, features 2.2, labels 2.0, upstream 2.1 oraz temporal 2.1 odtwarzają
pełne kwalifikowane źródła. Konsument odmawia importu producenta.
Przygotowanie kończy się przed assemblerem celów i fitowaniem. TEST zachowuje
wyłącznie licznik członkostwa; wektor celów i metryki modelu nie są otwierane.

Własne procesy producenta, konsumenta i archiwizacji mają wspólny pomiar co
0,2 s. Budżet: RSS 1280 MiB, scratch 640 MiB, 2700 s i 6 GiB wolnego na osobnym
runnerze. Scratch podniesiono prospektywnie z 576 do 640 MiB: wcześniejszy pomiar
ma 20% zapasu estymacji i dodatkowe 4% heurystycznego zapasu wzrostu zdarzeń.
To nie jest gwarantowana granica ani zaliczenie nowego źródła. Live stop obejmuje
wyłącznie własne drzewo procesów; odtwarzalne niezaliczone wejścia są usuwane.

Baseline `stockout-resource-baseline-30.json` pozostaje byte-exact z SHA256
`080fd9c2ec764a35e85e5a17ed0fd3447e0e0d318d960b8bff6546b564850239` i starym
producentem `08639e9188badb352ed64686a088fe237badad41`. Jego receipt nie zostaje
przepisany na nowe źródło. Wszystkie limity konsumenta pozostają bez zmian:
500000 wierszy wejść, 64 MiB wejść, 4 MiB kwalifikacji, 100000 ledger rows,
10000 fizycznych origins, 128 MiB bazy i 16 MiB development.
Lokalna polityka 50 GiB wolnego pozostaje bez zmian; ta ścieżka nie generuje
lokalnie dużych kohort.

Archiwum zawiera tylko siedem rzeczywistych rodziców konsumenta. Inwentarz,
rozmiary, tryby i SHA256 są porównane ze źródłem i ponownie z archiwum przed
publikacją checkpointu. Symlinki, pliki specjalne, zależności, `.git` i kod
producenta są wykluczone. Artefakty GitHuba są przechowywane 7 dni; ich pobranie
wymaga weryfikacji całego ZIP oraz każdego pliku i bezpiecznego odtworzenia.

Po kwalifikowanym przygotowaniu trzeba osobno zamrozić i zatwierdzić kampanię
końcową z prawdziwymi IDs źródeł, modelu, kalibratora, polityki i scenariuszy.
Dotychczasowe niezależne holdouty oraz nowe światy muszą mieć oddzielne raporty.
Niezaliczone wymagane bramki nadal blokują ready i promocję.
