# AI 09 — przygotowanie trzech wariantów bez powtórnej generacji ordinary

Nowa ścieżka v32 łączy bootstrap ordinary z przygotowaniem demand i physical.
Podczas już rozliczanego odczytu planera powstają kompletne receptury generacji
trzech wariantów. Typowane potwierdzenie zawiera te receptury, dzięki czemu
kolejny protokół można zamrozić na podstawie zapisanej dokumentacji operacji,
bez ponownego odczytu natywnego pakietu zawierającego truth. Starszy kontrakt
potwierdzenia v31 zachowuje swój zakres.

`compile_resolved_development_preparation` weryfikuje oba potwierdzenia
bootstrapu i wiąże jego niezmieniony dziennik. Zachowuje role, wersję runtime,
producer, locki, polityki, wcześniejsze koszty oraz pełne profile 25/50 z 365
dniami i wszystkimi lokalizacjami. Dopuszcza trzy operacje w ustalonej kolejności:

1. `reuse_ordinary_development_parent`: zapisany przed dostępem odczyt całego
   snapshotu i curated. Wykorzystuje oryginalną fazę `verify` w izolowanym
   procesie, ze sprawdzeniem proweniencji i niezależnym odtworzeniem curated.
   Wymaga dokładnie pierwotnego parenta. Potwierdzenie nazywa tę operację
   ponownym wykorzystaniem; nie deklaruje nowej generacji.
2. Oryginalne sześć faz generacji, kwalifikacji, eksportu, importu, kuracji
   i weryfikacji demand, z planem zapisanym przez natywnego planera.
3. Te same sześć faz dla physical, bez zmiany planu lub regeneracji ordinary.

Koszt zimnej generacji ordinary, planowania, nowego odczytu i obu nowych
generacji pozostaje widoczny. Łączny czas przygotowania nie może przekroczyć
zamrożonego limitu, maksymalnie 180 minut. Pozostały czas jest obliczany ze
świeżo odczytanego dziennika po rezerwacji operacji. Kontroluje go również
podstawowy `generate_campaign_parent`, więc bezpośrednie wywołanie tej funkcji
nie omija kosztów ani obowiązku weryfikacji zachowanego parenta. Dodatkowy
limit przekazany przez wywołującego może wyłącznie skrócić dozwolony czas.

Zachowano granice 12 GiB RAM, 8 GiB scratch, rezerwy 1 GiB RAM i 6 GiB dysku
oraz jeden wątek CPU. Awaria lub niedokończona rezerwacja nie zwraca budżetu
próby. Brakujące potwierdzenie blokuje następny etap przed dostępem do Source.
Ten dziennik nie udziela uprawnień do modeli, final, wyboru ani zamknięcia kampanii.

[Wyniki](09-91-resolved-development-variants.json) rozdzielają kontrolowane
testy wykonawcy od rzeczywistych wyników Source. Końcowa kontrola obejmuje
117 testów: dzienniki, oba rozmiary, kolejność operacji, powiązanie planów,
odmowy przy zmienionych danych i potwierdzeniach oraz wspólny budżet czasu.
Testy używają kontrolowanych wyników faz. Nie są dowodem generacji pełnych
profili ani realizacji scenariuszy. Przegląd po pierwszych testach wykrył
możliwość pominięcia sumowania kosztów przez bezpośredni generator; poprawkę
sprawdza dodatkowy test tej ścieżki. Starsze wyniki testów zachowano.

Source PR 113 ma pełny odbiór head i wynikowego main `95aa6f0e`:
21 sukcesów i cztery deklarowane pominięcia ścieżek. W Source PR 114 kontrola
danych planera trwa. Build obrazu API zatrzymał Docker Hub HTTP 429 przy
pobraniu niezmienionego, przypiętego obrazu bazowego. Nieudany log jest
zachowany; ponowienie ogranicza się do tej kontroli i jej zależności w zwykłym
Required CI. Pierwsze żądanie ponowienia dostało HTTP 403 przy nadal działającym
workflow; nie uruchomiono zastępczego workflow ani nie uznano tej kontroli za zaliczoną.

Nadal wymagane są odbiór natywnego planera, pełne CI tego kodu i publikacja
na main, rzeczywiste przygotowanie danych, powiązanie przygotowanych parentów
z uczciwymi próbami modeli i niezależnym wyborem finalistów, a następnie
pełna kampania trzech zastosowań. Nie uruchomiono kolejnego canonical,
pełnych małych profili Project, treningu Project ani nowych odczytów final.
AI 09 pozostaje `not_ready`.
