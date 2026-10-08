# AI 09: audyt przed kolejnym canonical

[Szósta próba](evidence/09-45-development-capacity-sixth-run.json) zakończyła
się przekroczeniem 8 GiB RSS po 2967.75 s, z zero ukończonych faz. Przed kolejną
próbą audytujemy pełną ścieżkę: generation, qualification, export, import, curation.
Oryginalne wymiary, seedy, daty, limity, locks, walidacja, sześć porażek i ich
koszty pozostają zachowane. Kolejny canonical wymaga odbioru wszystkich poprawek
i całego Required CI dokładnych head oraz wynikowych main w obu repozytoriach.

Source redukuje kopie i retencję wejść oraz symulatora, indeksuje zwroty,
przyjęcia i koszyki, ogranicza cache, serializuje i sprawdza porcje danych,
zwalnia własny build przed niezależnym odczytem staging i ogranicza pamięć
sortowania większych hashy. Pełne 58 tabel/CSV, zwykłe walidatory i niezależny
replay planów pozostają wymagane. Zmiany rozszerzają ścieżkę AI 09 i zachowują
wcześniejsze indeksy, cache, kopię tylko potrzebnych tabel oraz zwalnianie
zdarzeń wykonane w AI 08.

Po stronie AI wprowadzone są:

- Indeks SQLite historii planów dostawy po zamówieniu i wersji. Istniejący
  verifier nadal sprawdza pełne pokrycie wersji, chronologię i skumulowane ilości.
- Indeks mapowań zawierający produkt przed zakresem dat i osobny częściowy
  indeks starszych przypisań sklepów, ograniczony do `channel_assignments`.
  Punktowy dostęp do danych, niejednoznaczności i zasady czasu pozostają takie same.
- Ponowne użycie już zakodowanych bajtów w kanonicznym hashu i indeksie curated,
  bez ponownej serializacji tej samej wartości oraz bez zmiany content SHA.
- Jawne przejęcie świeżych tabel przez writer tylko dla przypiętej implementacji
  `planned-source-cached-execution-1.1.0`. Starsi producenci zachowują swoje API.

Finalna ścieżka AI zaliczyła 149 kontroli transportu, samodzielnej weryfikacji,
kuracji, truth isolation i watermarków; dodatkowe 88 kontroli obejmuje worker,
starsze backendy, frozen wire, nadzór zasobów i rzeczywiste plany zapytań SQLite.
Mypy, Ruff i format są obowiązkowe. Paczka instalacyjna i native kontrola pięciu
faz również wymagają odbioru dla końcowego head.

Mała para native zaliczyła 5/5 faz przed i po zmianach, z identycznymi hashami
58 tabel Source oraz 43 tabel eksportu/importu/curated. Trzy małe pary build
przed końcową redukcją retencji dały medianę CPU −13.11% i RSS −4.92%, ale nie
dowodzą wyniku canonical. Pierwsza mała para curation miała wyższy koszt CPU;
uwzględniamy także koszt utrzymania indeksów, a pomiar pełnego profilu pozostaje
rozstrzygający. Limity Source i transportu nie są podnoszone na podstawie prognoz.

Source 2.7/2.8, snapshot 1.1/1.2 i wcześniejsze kontrakty JSON są zachowane.
Końcowe trzy pary build dają CPU −16.78% i peak alokacji Python −5.18%, przy
retained Python +0.69% i RSS procesu +2.51%. Pełnego spadku RSS nie potwierdzamy.
Końcowy Source zaliczył 64 kontrole po ostatnim zmniejszeniu retencji, a końcowa
paczka AI zaliczyła 18 testów oraz rzeczywisty import/curation z identyczną treścią
43 tabel. Zweryfikowano bajty 597 modułów i 213 JSON, bez prywatnych cache i testów.
Nie wykonano nowych projektowych fitów, inicjalizacji dziennika ani odczytów
świeżego final testu. AI 09 pozostaje `in_progress / not_ready`; AI 07–08 oraz
wcześniejsze etapy pozostają READY i zamknięte. Otwarte sesje użytkownika
nie są zatrzymywane ani modyfikowane.
