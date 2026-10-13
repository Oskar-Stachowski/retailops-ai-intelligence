# Odtworzenie etykiet stockout na pełnym zbiorze AI 09

`CampaignStockoutLabels` łączy oryginalną prywatną kwalifikację Source z
całym zweryfikowanym publicznym snapshotem i jego ponownie odtworzonym
curated. Wywołująca kampania musi wcześniej zarezerwować odczyt obu pełnych
rodziców w zamrożonym dzienniku. Sam komponent nie nadaje prawa do odczytu
Project ani final.

Publiczne ruchy towaru, stany magazynowe i certyfikaty pokrycia trafiają do
prywatnego, usuwanego po użyciu indeksu SQLite. Każde okno z oryginalnego
pliku kwalifikacji musi odpowiadać dokładnie jednej publicznej parze
produkt–magazyn i chwili prognozy. Brakująca, dodatkowa lub powtórzona para
blokuje odbiór całego odczytu. Obejmuje to również okna niekwalifikujące się
do oceny i stany, w których brak towaru już trwa.

Obliczenie wykorzystuje niezmienione `LabelPreparation.point` i
`label_window` z AI 08. Zachowuje siedem dni horyzontu, dostępność etykiety,
kontrolę pokrycia oraz chwilowy brak towaru uzupełniony w tym samym czasie.
Porównuje status, wynik, powód niedostępności i czas dostępności z oryginalną
kwalifikacją. Nieznany wynik nie staje się etykietą ujemną.

Całe rodzicielskie pliki oraz wszystkie wiersze indeksu, także nieużyte
w zapytaniach, są ponownie sprawdzane przed odbiorem. Świadectwo powstaje
dopiero po pełnym odczycie i pomyślnym zamknięciu kontekstu. Częściowy odczyt,
przechwycony błąd, zmiana rodzica lub próba ponownego użycia blokują świadectwo.

Kontrakt v46 osobno wiąże plan odtworzenia, kod wykonania, limity całego
rodzica i politykę strumieniowego odczytu kwalifikacji. Limit 100 tys.
ruchów dotyczy jednej fizycznej pary produkt–magazyn. Nie ogranicza sumy
wszystkich ruchów całego projektu. Kontrola liczby ruchów następuje przed
dekodowaniem całej pary. Nie zmieniono limitów zamkniętego AI 08.
Indeks pozostaje ograniczony jawnie, domyślnie do 2 GiB. Cały proces wymaga
ponadto zewnętrznego nadzoru RAM, dysku, czasu i łącznego katalogu roboczego.

[Dowód 09.112](evidence/09-112-stockout-label-replay.json) potwierdza **176
testów źródeł i te same 176 testów z paczki**, bez pominięć, w tym regresje
publicznych cech. Mypy sprawdził 802 pliki; 669 modułów i 48 schematów mają
identyczne bajty w źródłach, paczce i instalacji. 129 modułów AI 08 i 416
wcześniejszych kontraktów pozostaje niezmienionych. Osobna polityka magazynu
etykiet jawnie opisuje użycie przyszłych wyników; domyślna polityka magazynu
cech pozostaje dotychczasowa.

Kontrole używają wcześniej ujawnionych fixtures Source 1.1/1.2 oraz osobnej
syntetycznej bazy 100 003 ruchów do sprawdzenia zapytań. Nie stanowią dowodu
pojemności pełnego profilu ani jakości modeli. Do połączenia pozostają pełne członkostwo
dojrzałych ról TRAIN/TUNE/CAL i niezależnej oceny, rezerwacje wszystkich
prób treningu, selekcja bez ukrytego ponownego uczenia oraz wykonanie całej
kampanii trzech zastosowań, pełny odbiór PR/main oraz końcowy audyt kosztów
powtarzanych odczytów. AI 09 nadal nie jest ready.

Canonical pozostaje wyłączony. Zapisany na głównej gałęzi limit RAM wynosi
**12 GiB**, z zachowaną rezerwą **1 GiB** dla systemu.

[Odbiór po połączeniu 09.115](evidence/09-115-stockout-final-parent-check.json)
potwierdza 239 testów źródeł i te same 239 z paczki. Usunięto dodatkowy
odczyt rodziców bezpośrednio przed zamknięciem właściciela Source. Właściciel
nadal sprawdza wszystkie pliki w czterech katalogach rodzicielskich przy zamykaniu,
a świadectwo powstaje dopiero po jego sukcesie. Kontrole na trzech zestawach
Source potwierdzają jeden końcowy przebieg oraz odmowę po zmianie nieużywanych
faktów sprzedaży w każdym z czterech rodziców. Nie jest to pomiar czasu
pełnego profilu; audyt pozostałych odczytów nadal pozostaje wymagany.
