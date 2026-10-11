# Pełna ocena progów i kolejki stockout AI 09

Natywny `stockout_policy.assessment.assess` odrzuca zbiór większy niż
10 tys. wierszy. Nowy `assess_full_stockout` zachowuje jego definicje
macierzy pomyłek, kosztów, progów, zaokrąglania ułamkowej pojemności oraz
rozstrzygania remisów. Przetwarza cały jawnie ograniczony zbiór do miliona
wierszy. Nie uruchamia treningu ani kalibracji.

Kolejka jest wybierana raz dla każdej chwili prognozy, łącznie dla wszystkich
produktów i magazynów. Kategorie, magazyny i grupy historycznych ograniczeń
zapasu raportują ten sam wybór. Nie otrzymują osobnych dodatkowych limitów.
Brak zadeklarowanej kategorii lub magazynu pozostaje widoczny jako pusta,
niekwalifikująca się do oceny grupa. Identyczne fizyczne klucze w różnych
zapisach tej samej chwili UTC są odrzucane.

Kontrakt v47 wiąże pełny oczekiwany zbiór kategorii i magazynów, parametry
progów, pojemność kolejki, koszty oraz limity wierszy, bajtów i chwil prognozy.
Brak klasy dodatniej nie daje idealnego recall. Każdy przekroczony limit
odrzuca całą operację, bez próbkowania ani dzielenia kolejki na fragmenty.

To komponent obliczeniowy. Wywołująca kampania musi oddzielnie zweryfikować
oryginalny model i predykcje, dojrzałość i kompletność etykiet, rolę czasową
oraz rezerwację dostępu. Wynik nie zatwierdza progów, wdrożenia ani jakości.
Nie przypisuje danym roli starego `test` z AI 08 i nie otwiera final.

[Dowód 09.113](evidence/09-113-stockout-full-assessment.json) obejmuje **51
testów źródeł i te same 51 testów z zainstalowanej paczki**, bez pominięć.
Kontrola obejmuje rzeczywiste 10 001 wierszy, zgodność wszystkich natywnych
wartości, brakujące grupy, limity i nieprawidłowe typy. Sprawdzenie typów
poprzedza serializację, która mogłaby zamienić skopiowane `true` w etykietę
liczbową `1`. Poprzedni nieudany test pozostaje zapisany w dowodzie.

Wszystkie 668 modułów oraz 48 schematów v33–v45 i v47 mają identyczne bajty
w źródłach, paczce i instalacji. 129 modułów zamkniętego AI 08 i 416
wcześniejszych kontraktów pozostaje niezmienionych. Kontrole numeryczne nie
zastępują oceny trzech zastosowań na pełnych danych, seedach i segmentach.
Połączenie z kampanią i pełny odbiór PR/main pozostają wymagane; AI 09 nadal
nie jest ready.
