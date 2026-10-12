# Dojrzałe role stockout na pełnym zbiorze AI 09

`CampaignStockoutMatureRows` łączy wszystkie rekordy odebranej paczki
[publicznych cech i upstream](ai09-stockout-public-preparation.md) z
[oryginalnymi etykietami](ai09-stockout-label-replay.md). Oba wejścia muszą
mieć tę samą pełną specyfikację Source oraz ten sam runtime. Plan przypina
również identyfikator publicznej paczki. Kampania wywołująca rezerwuje wcześniej
odczyt całego publicznego i prywatnego zbioru.

Kontrakt v48 jawnie przypisuje zastosowania stockout do pięciu wspólnych
okien development. Wspólny protokół kampanii musi dodatkowo sprawdzić,
że daty tych okien są identyczne z datami pozostałych zastosowań.

| Okno development | Zastosowanie stockout |
| --- | --- |
| `train` | `base_train`: preprocessing i sześć modeli bazowych |
| `early_stopping` | `calibrator_fit`: późniejsze uczenie kalibratorów |
| `tune` | `recipe_selection`: wybór modelu i kalibratora |
| `calibration` | `threshold_assessment`: ocena zamrożonej polityki progów i kolejki |
| `development_evaluation` | niezależna ocena wybranego pakietu |

To jawna polityka AI 09. Starszy podział AI 08 na TRAIN/TUNE/CAL/TEST pozostaje
niezmieniony. Final wymaga osobnej zamrożonej kampanii; komponent nie nadaje
uprawnienia do jego odczytu.

Dotychczasowy `CampaignOperationPlan` dopuszcza uczenie kalibratora wyłącznie
w roli `calibration`. Przed uruchomieniem całej kampanii nowa wersja wspólnego
protokołu musi jawnie obsłużyć powyższe przypisanie stockout. Nie wystarczy
zmienić nazwy wcześniejszego okna w starym dzienniku. Istniejący kontrakt
`OutcomePopulation` dotyczy wyłącznie forecastingu.

Łączenie odbywa się strumieniowo po pełnym kluczu produkt–magazyn–czas UTC.
Brakująca lub inna para zatrzymuje cały odczyt. Wynik zachowuje oryginalne
cechy i etykietę oraz przynależność do roli, kwalifikowalność, powód wykluczenia
i granicę dostępności etykiety. Rekordy spoza okien i z przerw między rolami
pozostają w wyniku jako `purged`.

Etykieta zachowuje natywny siedmiodniowy horyzont. Kwalifikowalność wymaga
dostępności wyniku najpóźniej w granicy wiedzy danej roli. Granica ta musi
przypadać ściśle przed początkiem następnej roli. Między oknami pozostaje
co najmniej 15 pełnych dni przerwy. Etykiety nieznane, trwający brak towaru
i niekwalifikujące się cechy zachowują swoje powody; nie stają się zerami.

Odbiór wymaga pełnego zużycia strumienia i pomyślnego zamknięcia kontekstu.
Kontrola publicznej paczki odbywa się po zamknięciu odtwarzania etykiet, aby
wykrywać także zmiany podczas końcowej kontroli rodziców. Częściowy odczyt,
przechwycony błąd, ponowne użycie, zmiana planu lub danych blokują świadectwo.
Wywołujący zapisuje rekordy w ograniczonych prywatnych plikach ról i dopiero
po odebraniu świadectwa może przekazać je zarezerwowanym operacjom treningu.

Limity obejmują wszystkie wiersze, pełny rozmiar strumienia i pojedynczy rekord.
Przekroczenie limitu przerywa odczyt. Domyślny limit to milion rekordów i 2 GiB
strumienia; komponent nie zbiera całej populacji w pamięci. Zewnętrzny nadzór
kampanii nadal odpowiada za RAM, dysk, czas i łączny katalog roboczy.

Świadectwo zachowuje liczebności wszystkich pięciu ról, obie klasy również
przy zerowej liczebności, powody wykluczenia, hash pełnego strumienia i odbiór
oryginalnych etykiet. Hash wszystkich faktycznie odczytanych etykiet, ich
liczba i klucze muszą zgadzać się z oryginalnym świadectwem. Dla każdej roli
osobno powstają hashe kluczy, etykiet i pełnych kwalifikujących się rekordów;
hashe kluczy i etykiet zachowują format natywnych wejść treningowych AI 08.
Samo przeliczenie członkostwa nie dowodzi wystarczającej
próby, jakości modeli ani uprawnień dziennika. Operacje treningu, selekcji bez
dodatkowego uczenia, oceny trzech zastosowań, MLflow i lifecycle nadal wymagają
połączenia we wspólnym protokole. AI 09 pozostaje `not_ready`.

[Odbiór 09.116](evidence/09-116-stockout-mature-roles.json) potwierdza
142 testy kodu i te same 142 testy z zainstalowanej paczki, bez pominięć.
672 moduły oraz 50 schematów mają identyczne bajty w źródłach, wheelu
i instalacji. Kontrole obejmują trzy rzeczywiste małe zestawy Source,
podmianę treści etykiety przy zachowaniu jej klucza, późne zmiany rodziców
oraz osobny syntetyczny strumień 10 001 wierszy. Ten strumień nie jest
pomiarem wydajności generacji Source ani pełnej kampanii. Odbiór PR/main
i wykonanie zarezerwowanych operacji Project pozostają wymagane.
