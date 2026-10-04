# AI 09.6 — przygotowanie niezależnego podziału prognoz

Status: **mechanika przygotowania odebrana lokalnie; ocena modeli not_ready**.
Dotychczasowy [benchmark](forecast-development-comparison.md) zachowuje swój
diagnostyczny zakres: validation służy także early stopping. Nowy moduł nie
przepisuje jego wyników na niezależną ocenę i nie przełącza starego runnera.

## Pięć ról

| Rola | Przyszłe dozwolone zastosowanie |
| --- | --- |
| `train` | Dopasowanie preprocessingu i parametrów modelu |
| `early_stopping` | Zatrzymanie treningu, bez wyboru receptury na późniejszych danych |
| `tune` | Wybór receptury przed kalibracją |
| `calibration` | Dopasowanie kalibratora już wybranej receptury |
| `development_evaluation` | Niezależna ocena po zamrożeniu modelu i kalibratora |

Daty są jawne i chronologiczne. Każde sąsiednie okno oddziela co najmniej
15 wyłączonych originów: horyzont do 14 dni i proponowany jeden dzień
opóźnienia etykiety. Większe opóźnienie wymaga odpowiednio dłuższej przerwy.
Cutoff etykiet musi pozwalać dojrzeć ostatniemu targetowi i nie może sięgać
chwili prognozy pierwszego originu następnej roli. Te reguły sprawdzają
możliwość czasową; faktyczna kompletność i dostępność etykiet nie są jeszcze
kwalifikowane. Chronologiczne rozdzielenie nie oznacza niezależności
statystycznej obserwacji.

Szablon proponuje 30 dni treningu i po 10 dni czterech późniejszych ról,
czyli minimum **130 dni originów**. To propozycja dat, nie odbiór wielkości
próby. Techniczne minimum pięciu jednodniowych ról wynosi 65 dni originów.
Obecny 60-dniowy zbiór projektu nie spełnia nawet tego minimum i jest
odrzucany. Nie skracamy purge ani ról, aby zmieścić stare dane. Do liczby
originów dochodzą historia potrzebna do cech oraz dojrzenie ostatnich etykiet.

## Artefakt i odczyt

`prepare_partitions` weryfikuje istniejące publiczne cechy i kalendarz.
Weryfikacja cech obejmuje także ich historyczne obserwacje i lineage;
przygotowanie nie przyjmuje ścieżki curated, splitu ani zbioru etykiet,
nie odczytuje etykiet targetów i nie wykonuje treningu lub metryk.

Artefakt przypina pełny descriptor cech, wszystkie moduły Python pakietu,
lock zależności, wersję Pythona oraz pięć okien i cutoffy. Każdy istniejący
pełny klucz prognozy trafia dokładnie raz do jednego z sześciu plików:
pięć ról oraz `purged`. Klucz obejmuje produkt, miejsce sprzedaży, kanał,
origin, target, horyzont i obie konwencje czasu. Zachowujemy również
rzeczywiste krótsze populacje horyzontów przy końcu dostępnego asortymentu;
nie dopisujemy obserwacji ani nie wybieramy przecięcia kluczy.

Każde przypisanie zawiera hash pełnego wiersza cech, ale nie jego wartości
ani wynik sprzedaży. Weryfikator porównuje cały zbiór z ponownie zweryfikowanym
rodzicem. Podmienione klucze, role, cutoffy lub hashe są odrzucane także po
przeliczeniu wszystkich publicznych checksumów. Zmiana kolejności wejścia
nie zmienia tożsamości. Publikacja nie nadpisuje istniejącego artefaktu;
katalogi są prywatne 0700, pliki 0600, zapis obejmuje fsync.

Indeks wykorzystuje istniejący zweryfikowany forecast reader i SQLite
z cache 4 MiB; nie tworzymy drugiego czytnika stockout z AI 08.
Odczyt roli najpierw sprawdza prywatną kopię na dysku, potem zwraca
pojedyncze przypisania, bez materializacji całej roli w RAM. Limity tego
przyrostu wynoszą 100 000 kluczy, 128 MiB kompletnego artefaktu i 128 MiB
indeksu; przekroczenie blokuje całą populację, zamiast ją obcinać.
Nie są to limity ani odbiór całego przyszłego pipeline'u `ai-training`.

`require_role_purpose` i `role_memberships` odrzucają użycie np. tune do
preprocessingu lub early stopping do wyboru receptury. To kontrola roli,
nie zezwolenie na fit lub odczyt etykiet. Żądanie niezależnej oceny jest
zawsze blokowane przed odczytem wejść: potrzebny jest osobny audyt dostępu
do wyników i zamrożony protokół. `holdout_freshness` pozostaje jawnie
niepotwierdzone. Żadne przeliczenie checksumów nie włącza oceny ani promocji.

## Polecenia

Przykład dotyczy przyszłego kalendarza cech z co najmniej 130 originami.
Daty i długości trzeba przypiąć przed dopasowaniem modeli.

```bash
.venv/bin/python -m retailops_ai.evaluation_campaign.partition_cli template \
  --start 2026-01-01 --end 2026-05-10 > /private/tmp/ai09-partition-policy.json

.venv/bin/python -m retailops_ai.evaluation_campaign.partition_cli prepare \
  --features /private/tmp/qualified-features/features-sha256-HASH \
  --policy /private/tmp/ai09-partition-policy.json \
  --output-root /private/tmp/ai09-independent-partitions

.venv/bin/python -m retailops_ai.evaluation_campaign.partition_cli preflight \
  --features /private/tmp/qualified-features/features-sha256-HASH \
  --partitions /private/tmp/ai09-independent-partitions/ai09-partitions-sha256-HASH
```

`prepare` i `verify` zwracają 0 dla poprawnego przygotowania. `preflight`
zwraca **3 / evaluation_status=not_ready**. Błąd wejścia lub artefaktu
zwraca 2 z komunikatem bez treści danych. Trzy schematy v4 są dostarczane
w wheel i sprawdzane przez standardowe `contracts-check`.

## Odbiór i dalsza praca

[Receipt 09.6](evidence/09-06-independent-forecast-partitions.md) opisuje
kontrolny fizyczny fixture 65 originów, replay natywny i z odłączonego wheel,
negatywne przypadki oraz granice pomiaru. Próbka potwierdza działanie kodu,
nie jakość modelu, świeżość holdoutu ani kompletny source handoff.

Następny przyrost powinien dodać audyt odczytu etykiet i potwierdzenie,
których danych jeszcze nie użyto do decyzji. Następnie potrzebne są
czytnik etykiet ograniczony do konkretnej roli, kwalifikacja dojrzałości,
pięciorolowy protokół treningu związany z rejestrem prób i kalibracja.
Stary rejestr 09.5 obsługuje dotychczasowy diagnostyczny benchmark;
sam nowy podział nie rozszerza jego zakresu. Końcowe wejścia i polityki
AI 07–08 oraz audyt portfolio final testu pozostają otwarte.
