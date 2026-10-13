# Uruchamianie oryginalnego przygotowania źródeł AI 09

`scripts/prepare_ai09_sources.py` łączy zamrożone polityki z istniejącymi
kontrolerami przygotowania danych. Obsługuje oddzielne profile 25, 50 oraz
pełne canonical 100 produktów. Każdy zachowuje 365 dni historii, 5 par
sprzedażowych, 3 lokalizacje zapasu oraz zwykły wariant, demand i physical.

Jedno wywołanie może wykonać pięć oryginalnych operacji:

1. Pełna generacja ordinary wraz z kwalifikacją, eksportem, importem,
   kuracją i niezależną weryfikacją.
2. Natywne planowanie scenariuszy na oryginalnych danych ordinary.
3. Osobno rozliczone ponowne sprawdzenie ordinary dla dalszego przygotowania.
4. Pełna generacja demand według oryginalnego planu.
5. Pełna generacja physical według oryginalnego planu.

Wykonawca wywołuje istniejące kontrolery, rezerwacje i pomiary. Nie tworzy
zastępczej generacji ani nowej implementacji algorytmów Source. Każda próba
pozostaje w pierwotnym dzienniku; zwykłe źródło nie jest generowane drugi raz.

Przed startem potrzebne są [zamrożone polityki](ai09-prospective-policies.md)
z bieżącym runtime, czysty checkout przypiętego Source oraz jego osobny
interpreter spełniający oryginalne wymagania API i eksportu Parquet.
Wykonawca wyprowadza skróty zależności i natywnego planera z rzeczywistego
kodu. Odczyt metadanych sprawdza także zainstalowane wersje zależności
przez oryginalną funkcję workera generacji.

Prywatny plik opcji zgodny z `SourcePreparationOptions` podaje:

- profil 25/50/100, bezwzględne ścieżki Source i jego interpretera;
- dokładny podział dat na train, early stopping, Tune, Cal i ocenę development;
- osobne limity generacji, planowania i ponownej weryfikacji;
- wspólny limit czasu wszystkich pięciu operacji, najwyżej 10800 sekund.

Zachowane są sufity 12 GiB RSS i 8 GiB scratch oraz rezerwy co najmniej
1 GiB dostępnego RAM i 6 GiB dysku. Limity nie są zwiększane przy wznowieniu.
Wspólny budżet uwzględnia oryginalny koszt generacji i planowania przed
uruchomieniem dalszych wariantów.

```sh
python scripts/prepare_ai09_sources.py \
  --root /private/campaign/source-25 \
  --options /private/campaign/source-25-options.json \
  --policies /private/campaign/development-policies.json \
  --maximum-new-operations 1
```

Katalog sesji ma uprawnienia `0700` i nie może nachodzić na checkout Source.
Przed pierwszą operacją powstaje niezmienny `source-preparation.json`,
zawierający opcje, oryginalne polityki i przypięcie producenta. Domyślny limit
pięciu nowych operacji wykonuje cały pozostały program; wartość 1 umożliwia
zatrzymanie między operacjami. Log wskazuje ich rozpoczęcie, ponowne
sprawdzenie i zakończenie.

Wznowienie sprawdza oryginalne receipts, manifesty rodziców, plik natywnych
planów i koszt każdego ukończonego kroku. Błąd, niedokończona rezerwacja,
zmiana polityk/ścieżek albo brak dowodu blokuje dalszy start. Zapisy intencji
inicjalizacji zapobiegają odtworzeniu utraconego dziennika bez wcześniejszych
prób i kosztów.
Wykonawca nie przejmuje wcześniejszego dziennika ani nie ponawia prób
automatycznie. Osobna blokada sesji chroni przed drugim wykonawcą.

Po zakończeniu `source-preparation-result.json` zawiera oryginalny
`PreparedDevelopmentParents` lub `PreparedCanonicalDevelopmentParents`
oraz ścieżki raw/snapshot/curated dla wszystkich trzech wariantów.
Ponowny odbiór ukończonej sesji nie uruchamia workerów ani nie zmienia kosztów.
Ten wynik jest wejściem do [wykonania prognoz](ai09-development-execution.md);
do uruchomienia treningów nadal potrzebny jest ich oddzielny zamrożony protokół.

Uruchomienie programu na rzeczywistych danych Project pozostaje oddzielnym
krokiem od jego testów. Pliki przygotowania nie nadają uprawnień do fitu,
final test, promocji modeli ani oznaczenia AI 09 jako `ready`.

[Odbiór komponentu 09.103](evidence/09-103-source-preparation-execution.json)
zapisuje 111 kontroli źródeł i te same 111 z paczki, bez pominięć. W tym
19 kontroli nowego wykonawcy obejmuje wszystkie pięć operacji dla 25/50/100,
zatrzymanie po każdym kroku, zachowanie kosztów, błędy, utratę dziennika,
zmienione manifesty i plany, niezgodne opcje oraz równoczesnego właściciela.
Mypy sprawdził 784 pliki; 652 moduły i 30 plików kontraktów mają zgodne bajty
w instalacji. Żaden z 396 wcześniejszych kontraktów nie został zmieniony.

Pełne kontrole Ruff, formatowania 1361 plików, odsyłaczy dokumentacji,
kontraktu Required CI oraz skan historii i drzewa przez Gitleaks przeszły.
Osobno sprawdzono publiczny odczyt metadanych i oryginalne kompilatory
planowania 25/100 na rzeczywistych przypięciach zaakceptowanego Source.
Nie inicjalizowano przy tym dzienników ani danych Project.
