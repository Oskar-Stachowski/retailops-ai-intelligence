# Polityki AI 09 przed generowaniem danych

`contracts/evaluation/v38/development_policies.default.json` zawiera rzeczywiste
reguły oceny, z których powstają skróty `DevelopmentPlanningContext`.
Usuwa zależność wcześniejszego formatu od identyfikatorów dwunastu źródeł,
które można wyznaczyć dopiero po natywnym planowaniu scenariuszy. Format v27
i wcześniejsze kontrakty pozostają zachowane.

Nowy `CampaignRequiredGroupRecipe` ustala wymagane grupy dla ordinary,
demand i physical przed generowaniem. Każdy wariant nadal wymaga wyników
globalnych, wszystkich horyzontów, kanałów i ośmiu kategorii. Ordinary
odpowiada za normalny popyt, promocje oraz pozostałe przekroje historii,
dostępności, zapasu, lead time, wolumenu i clean controls. Demand odpowiada
za demand shock i trzy typy anomalii popytu; physical za inventory constraint,
return spike i inventory-censored episode. Te same reguły obowiązują każdy
seed; suma wymaganych grup zachowuje wcześniejsze krytyczne pokrycie.

Wszystkie grupy diagnostyczne nadal są raportowane. Pusta wymagana grupa
blokuje kwalifikację. Wykonawca oceny sprawdza oryginalny protokół, plan,
wariant, seed, rolę oraz spis populacji. Nie dobiera właściciela grupy
na podstawie uzyskanej liczebności lub wyniku modelu. Nowy receipt zachowuje
oryginalne dokumenty i jest sprawdzany ponownie przy odczycie artefaktów.

Zestaw domyślny przypina:

- osiem kategorii wyznaczonych przez natywną funkcję `_categories()` z Source
  `d2363a5c78e0b190132e5e3d804da73b3136487d`, wraz ze skrótami czterech plików kodu;
- dotychczasowe progi jakości forecastingu i politykę niepewności: bloki
  28 dni, co najmniej 8 bloków i 30 serii, 999 replikacji;
- numeryczną politykę referencyjną anomalii z obu odebranych archiwów AI 07
  oraz oryginalne reguły obserwacji, epizodów, deduplikacji i brakujących metryk;
- natywną definicję dojrzałej etykiety stockout, progi 25/50/90% oraz budżet
  obsługi 50% fizycznych kluczy z przyjętej polityki AI 08;
- kryteria stockout AP, Brier, kompletności segmentów oraz ECE do 0,15.
  To prospektywny wybór ścisłej reguły; nie przenosi wyjątku ostrzegawczego
  ECE do 0,20 dla małych kategorii z wcześniejszej kampanii AI 08;
- pełne wymiary stockout: 8 kategorii, 3 lokalizacje development i 4 final,
  oraz jednakowe wagi seedów i scenariuszy.

Referencje AI 07/08 nie kwalifikują nowych danych ani modeli. Anomaly zachowuje
oznaczenie zakresu oryginalnej polityki AI 07; nowa ocena AI 09 wymaga osobnego
raportu z właściwymi źródłami. Dla stockout trzeba osobno dopiąć rzeczywisty
model, kalibrację i ocenę operacyjną. Nie przenosi się starego modelu ani
zatwierdzenia lifecycle przez samo przepisanie progów.

Polecenie wymaga czystego checkoutu dokładnie przypiętego producenta oraz
istniejącego prywatnego katalogu docelowego:

```sh
python scripts/freeze_ai09_development_policies.py \
  --producer /private/source-pinned \
  --output /private/campaign/development-policies.json
```

Przed zapisem sprawdzane są commit, pliki producenta i wynik natywnego odczytu
metadanych kategorii. Dane Source nie są generowane ani odczytywane. Po
wygenerowaniu rodziców nadal wymagane jest sprawdzenie całego rzeczywistego
katalogu produktów; deklaracja metadanych go nie zastępuje.

Plik zapisuje się jednokrotnie z uprawnieniami `0600`. Zawiera pełne polityki,
przypięcie bieżącej implementacji oraz historyczne koszty: 44 opublikowane
starty fitu, wycofane niedostępne sloty i nieznany pełny koszt historii.
Nieznanych kosztów nie zamienia na zero. Odczyt odrzuca zmieniony runtime,
a walidacja odrzuca rozbieżność między polityką, jej skrótem i kontekstem.

Przed pierwszą generacją trzeba przypiąć ten kontekst do oryginalnego
dziennika przygotowania. Sam plik nie nadaje uprawnień do fitu, final test,
promocji ani `ready`. Automatyczne przygotowanie pełnej kampanii, jej
rzeczywiste wykonanie i końcowy odbiór trzech zastosowań pozostają wymagane.

[Odbiór komponentu 09.102](evidence/09-102-prospective-development-policies.json)
zapisuje 88 testów źródeł i te same 88 z paczki, bez pominięć. Kontrole obejmują
każdy seed i wariant, odrzucenie zmienionych źródeł/polityk, puste grupy,
oryginalne workery oceny i ponowną weryfikację raportów. Dane i modele
w tych testach są jawnymi zastępnikami. Osobno sprawdzono metadane kategorii
na rzeczywistym zaakceptowanym commicie Source. Mypy objął 780 plików,
649 modułów i 28 plików kontraktów mają zgodne bajty w instalacji,
a 391 wcześniejszych kontraktów pozostało niezmienionych.
