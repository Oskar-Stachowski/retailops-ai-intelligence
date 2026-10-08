# AI 09: audyt przed kolejnym canonical

[Audyt zapytań i przygotowanie 09.54](evidence/09-54-ledger-query-capacity-preparation.json)
podłącza Source `a7b850a0` z indeksami zweryfikowanych ksiąg, okresów fizycznych
i jednostek oraz niezależnymi koszykami dziennego verifiera. Małe kontrolne
procesy zachowują wszystkie porównane hashe; zapis obejmuje również wzrost
pamięci indeksów i wcześniejsze pomiary. 274 kontrole Source, 106 AI i 22
z paczki przeszły. [Receptura 1.9](reference/ai09-development-capacity-v1.9.json)
przypina ten audyt, pełny profil i 12 GiB. Pozostaje nieuruchomiona do pełnego
odbioru dokładnych head i wynikowych main obu repozytoriów.

[Receptura 1.8](reference/ai09-development-capacity-v1.8.json) ma na polecenie
użytkownika limit RSS drzewa **12 GiB** (12 884 901 888 B) i rezerwę 1 GiB.
PR60 scalono normalnie jako `4ce8a3cf`; pełne CI dokładnego head i tego main
zakończyły się **17/17 success**, przed pojedynczym uruchomieniem pomiaru.
[Wynik ósmej próby 09.53](evidence/09-53-development-capacity-eighth-run.json)
wiąże zweryfikowany artefakt [run 37824794411](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37824794411): **`wall_limit`**,
0/5 ukończonych faz, 3600.58 s.
Próbkowany peak drzewa wyniósł 9066307584 B, a dolna granica CPU workera 3599.61 s.
Profil 365 × 100 × 5 × 3, Source, walidacja i pozostałe budżety zachowano.
Zachowano siedem wcześniejszych porażek, receptury 1.0–1.7 i nieuruchomioną 1.6.
Nie wykonano automatycznego retry. Wynik nie kwalifikuje pełnego ai-training,
modeli ani poprzedniego limitu 8 GiB. Nowe projektowe fity i odczyty świeżego
final wynoszą zero; AI 09 pozostaje `not_ready`.

[Szósta próba](evidence/09-45-development-capacity-sixth-run.json) zakończyła
się przekroczeniem 8 GiB RSS po 2967.75 s, z zero ukończonych faz. Przed
siódmą próbą przeprowadzono audyt pełnej ścieżki: generation, qualification,
export, import, curation.
Oryginalne wymiary, seedy, daty, limity, locks, walidacja, sześć porażek i ich
koszty pozostają zachowane. Wszystkie poprawki i pełne Required CI dokładnych
head oraz wynikowych main obu repozytoriów odebrano przed pojedynczym dispatch 1.7;
[receipt 09.50](evidence/09-50-audit-acceptance-capacity-seventh-start.json) zapisuje ten odbiór.

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
  `planned-source-cached-execution-1.1.0`, `1.1.1` i audytowanej `1.1.2`.
  Starsi producenci zachowują swoje API.

Finalna ścieżka AI zaliczyła 149 kontroli transportu, samodzielnej weryfikacji,
kuracji, truth isolation i watermarków; dodatkowe 88 kontroli obejmuje worker,
starsze backendy, frozen wire, nadzór zasobów i rzeczywiste plany zapytań SQLite.
Mypy, Ruff i format są obowiązkowe. Paczka instalacyjna i native kontrola pięciu
faz zostały odebrane dla końcowego head.

Mała para native zaliczyła 5/5 faz przed i po zmianach, z identycznymi hashami
58 tabel Source oraz 43 tabel eksportu/importu/curated. Trzy małe pary build
przed końcową redukcją retencji dały medianę CPU −13.11% i RSS −4.92%, ale nie
dowodzą wyniku canonical. Pierwsza mała para curation miała wyższy koszt CPU;
uwzględniamy także koszt utrzymania indeksów, a pomiar pełnego profilu pozostaje
rozstrzygający. Limity Source i transportu nie są podnoszone na podstawie prognoz.

Source 2.7/2.8, snapshot 1.1/1.2 i wcześniejsze kontrakty JSON są zachowane.
Pierwszy Required CI tego audytu wykrył nieaktualną sumę kontrolną modelowego
importera w deklaracji właściciela. [Aktualizacja pinu](evidence/09-47-capacity-audit-owner-repin.json)
zachowuje poprzednią deklarację, oryginalną kopię `source_snapshot_native`,
historyczne piny i wszystkie kontrole integralności. Z 425 wcześniejszych JSON
424 zachowują dokładne bajty; zmienia się wyłącznie bieżąca deklaracja właściciela.
Cały `make contracts-check` oraz 90 testów z drzewa i 90 z nowej paczki przeszły.
Pełne CI obu repozytoriów odebrano przed wykonaniem siódmej próby.
Końcowe trzy pary build dają CPU −16.78% i peak alokacji Python −5.18%, przy
retained Python +0.69% i RSS procesu +2.51%. Pełnego spadku RSS nie potwierdzamy.
Końcowy Source zaliczył 64 kontrole po ostatnim zmniejszeniu retencji, a końcowa
paczka AI zaliczyła 18 testów oraz rzeczywisty import/curation z identyczną treścią
43 tabel. Zweryfikowano bajty 597 modułów i 213 JSON, bez prywatnych cache i testów.
Nie wykonano nowych projektowych fitów, inicjalizacji dziennika ani odczytów
świeżego final testu. AI 09 pozostaje `in_progress / not_ready`; AI 07–08 oraz
wcześniejsze etapy pozostają READY i zamknięte. Otwarte sesje użytkownika
nie są zatrzymywane ani modyfikowane.

Dodatkowa kontrola przed pomiarem wykryła zachowany pierwotny kandydat sprzedaży
podczas drugiej projekcji. Source `2.2.1` / planned `1.1.1` zwalnia go po pełnej
niezależnej rekonsyliacji, pozostawiając cztery publikowane prywatne tabele.
[Dowód 09.49](evidence/09-49-projection-retention-capacity-preparation.json)
zawiera trzy kontrole czasu życia i 67 regresji native. Trzy świeże pary procesów
z zapisanym kodem zachowują wszystkie 58 tabel i context; mediany małego build
to Python peak −16.50%, RSS −8.09%, CPU −1.65% i retained Python −0.04%.
To dodatkowe porównanie względem pierwszego audytu, a nie pełnego canonical.

AI zaliczył 95 kontroli, 41 z nowej paczki, Mypy 718 plików, Ruff/format
1226 plików i cały odbiór kontraktów. Zweryfikowano identyczność 597 modułów
w paczce. Nowa receptura 1.7 zachowuje nieuruchomioną 1.6 oraz wszystkie sześć
rzeczywistych porażek. Pełne CI head i wynikowych main obu repozytoriów
przeszło przed wykonaniem diagnostyki. Pierwszy audyt jest już scalony: Source PR108,
AI PR56–57. Żaden z tych odbiorów nie kwalifikuje jeszcze pełnej kampanii.

Końcowe poprawki są na Source main `c04ca954` i AI main `89d6c8f2`. Dokładne
head PR109/PR58 oraz oba wynikowe main mają pełne zaakceptowane CI: Source
21 success i cztery deklarowane scoped skips, AI 17/17 success.
[Dowód odbioru 09.50](evidence/09-50-audit-acceptance-capacity-seventh-start.json)
zawiera inventory zadań, referencje i sumy plików sprawdzonych przed pojedynczym
dispatch siódmej próby [37807749014](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37807749014).
1.6 pozostaje nieuruchomiona; wszystkie wcześniejsze koszty są zachowane.
Odbiór kodu i start runnera nie są wynikiem zasobów ani kwalifikacją modeli.

[Wynik siódmej próby 09.51](evidence/09-51-development-capacity-seventh-run.json) wiąże odebrany i zweryfikowany artefakt [run 37807749014](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37807749014).
Pomiar zakończył się `tree_rss_limit`, z 0/5 ukończonych faz. Cały przebieg trwał 1702.79 s;
próbkowany peak drzewa wyniósł 8635432960 B, a dolna granica CPU workera 1701.93 s.
Pełna pojemność pozostaje niepotwierdzona. Zachowano koszt porażki; nie wykonano automatycznego retry.
Pełny profil 365 × 100 × 5 × 3, limity, rezerwy i wcześniejsze koszty zachowano.
Nowe projektowe fity i odczyty świeżego final wynoszą zero; AI 09 pozostaje `not_ready`.

Ostatni z 14 ograniczonych zapisów stosu obejmuje `InventoryLedger.from_payload`
podczas niezależnej `reconcile_simulation` / `reconcile_source_commerce`.
To lokalizacja wykonywania, nie dowód właściciela alokacji ani dokładny stos chwili
zatrzymania. Przed kolejnym canonical trzeba zmierzyć tę część na odsłoniętych
kontrolach, zachować wszystkie walidatory i odebrać ewentualne poprawki oraz pełne CI.
