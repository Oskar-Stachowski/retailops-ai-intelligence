# AI 08.4 — porównanie modeli na development

Przyrost dopasowuje LR/HGB, wykonuje porównanie z upstream i bez niego
oraz kontrolę dwóch cech ze sprzedaży w dniach dostępności towaru.
[Kontrakt](../reference/stockout-training.md) zamraża konfiguracje,
czasowe role, ograniczenia kalibracji, metryki i ilustracyjne capacity.

Bazą jest `aa6b9537271f9d13666bccb8ff76ec99bfa103f0` z
[odebranym historycznym upstream](08-03-stockout-upstream.md).
Ponownie wykorzystano niezmienioną próbkę 102 dni, bez generowania
nowych danych. Final test zachowuje wyłącznie członkostwo; nie oceniano
jego outcomes i nie używano ich do wyboru modelu lub kalibracji.

[Wersjonowany zapis](08-04-stockout-models.json) przypina rodziców, recipe,
implementację, klucze development, model IDs, pomiary i wyniki.

## Dane i wybór na tune

Train obejmuje 340 punktów (193/147 klas 0/1), tune 135 (73/62),
calibration 130 (67/63). Wszystkie 605 punktów development mają upstream;
131 punktów późniejszego testu pozostaje wyłącznie członkostwem.
Globalna kompletność upstream dla pełnych bazowych origin pozostaje false,
zgodnie z rodzicem; nie przepisano historycznej bramki.

| Rodzina / wariant | Tune AP | Tune Brier |
|---|---:|---:|
| LR bez upstream | 0,990402 | 0,029950 |
| LR z upstream | 0,992184 | 0,029693 |
| HGB bez upstream | **0,994294** | **0,029300** |
| HGB z upstream | 0,986248 | 0,029339 |
| LR kontrola raw sales | 0,988746 | 0,030149 |
| HGB kontrola raw sales | 0,992827 | 0,029316 |

Zamrożona receptura wybiera provisional HGB bez upstream według tune AP.
Różnice są niewielkie; to nie jest dowód przewagi na pełnym profilu.
Kontrola cenzorowania usuwa tylko dwie cechy ze zweryfikowanych dni
in-stock, zachowując historyczne flagi zapasu. Nie bierze udziału w wyborze.
Wszystkie warianty zachowują identyczny hash kluczy tune i 0 własnych odrzuceń.

No-skill AP wynosi 0,459259 (prevalence siedmiodniowej etykiety tune).
Nie jest to dzienny stockout rate. Globalne ROC-AUC wybranego wariantu
wynosi 0,995250; na mniejszym materiale nie traktujemy go jako głównej bramki.
Z 8 segmentów kategorii **5 ma jedną klasę** i ranking `not_evaluable`.
Pozostałe małe segmenty mają policzalne metryki, bez deklaracji wymaganej
liczebności do końcowej kwalifikacji. Dobowe okna 7 dni mogą pokrywać
wspólny epizod; nie są niezależnymi zdarzeniami.

Stałe top 20% per origin z zaokrągleniem w górę daje w tune 32 priorytety
spośród 135 punktów. Każdy wariant znajduje 32 z 62 dodatnich okien:
recall 0,516129, precision 1,0, 30 pominiętych dodatnich okien.
Eksploracyjny koszt to 150 jednostek (FP=1, FN=5). To ilustracja capacity,
nie zatwierdzona polityka operatora ani ekonomiczna oszczędność.
Raport wiąże licznik każdego origin i osobną tabelę pięciu stałych progów;
nie dopasowano risk bands ani threshold_version do serving.

## Dopasowanie sigmoid

Każdy z 6 modeli dostał oddzielny sigmoid na 130 punktach calibration;
wszystkie nachylenia są dodatnie. Dla wybranego HGB Brier raw na calibration
wynosi 0,075505, a po dopasowaniu sigmoid **0,053564 in-sample**.
Lepszy wynik na tych samych danych nie zalicza niezależnej bramki kalibracji.
Nie wybrano ponownie rodziny według tych wyników: np. LR bez upstream
ma in-sample Brier 0,037205, ale pozostaje wariantem porównania.
Testy potwierdzają, że zamiana etykiet calibration nie zmienia train
preprocessingu, parametrów modeli ani wyboru na tune.

## Kontrole i zasoby

Regresja training/split/upstream ma **47/47 testów w 9,13 s**, w tym 19
nowych przypadków. Obejmuje train-only preprocessing, przyszłe braki,
nieznane kategorie, PIT kategorii, ochronę testu, wybór na tune,
matematykę capacity/metryk i eksport ujemnych log odds HGB bez clippingu.
Przy uruchomieniu w sandboxie biblioteka ostrzegła o niedostępnej informacji
o fizycznych rdzeniach i użyła liczby logicznych; recipe ma limit jednego wątku.

Natywne CLI build/rebuild/verify trwało **98,99 / 88,46 / 97,64 s**
łącznie z replay rodziców. Wynik ma 864 342 bajty i prywatny tryb 0600.
Powtórna budowa ma identyczne ID i bajty. Bez zgody na weryfikację
prywatnego źródła etykiet CLI odrzuciło operację przed odczytem rodziców.
277 plików publicznego/prywatnego snapshotu, curated i przygotowanych
artefaktów pozostało niezmienionych. Maksimum RSS pojedynczego potomnego
procesu natywnego wyniosło 322 797 568 bajtów, około 307,8 MiB.

Wheel ze sdist, zainstalowany bez zależności do osobnego katalogu,
uruchomiono spoza repo; pakiet producenta `data` był niedostępny.
Build/rebuild/verify trwało **99,80 / 103,34 / 98,82 s**.
Wynik ma identyczne ID i bajty jak odbiór natywny, z trybem 0600.
Celowo zmieniono wagę LR i przeliczono model IDs oraz wszystkie manifestowe
hashe; pełny replay odrzucił zmianę w 101,92 s. Wejścia pozostały niezmienione.
RSS pojedynczego dziecka wyniósł 314 540 032 bajty, około 300,0 MiB.
Pomiary RSS są kumulacyjnym maksimum `RUSAGE_CHILDREN`, nie sumą drzewa
ani odbiorem budżetu pełnego profilu treningowego.

Osobny pomiar samego porównania, po sprawdzeniu przypiętych wejść,
obejmuje 6 fitów modeli, 6 sigmoidów i wszystkie metryki: **0,465793 s wall**
oraz **0,385635 s CPU** procesu. Wynik ma dokładnie te same bajty i ID.
Timer wyklucza replay rodziców i wcześniejsze ładowanie danych/bibliotek.
Pełne CLI jest zdominowane przez odtworzenie i weryfikację danych,
bez deklaracji podobnego kosztu pełnego `ai-training`.

Rzeczywiste polecenia odbioru to `python -m retailops_ai.stockout_training.cli
build` oraz `verify`, z publicznym curated `6fade44...`, pięcioma
niezmienionymi plikami przygotowania z odbioru 08.3 i prywatnym source
`dbdddbc...`, z jawnym `--allow-evaluation-truth`.
[Kontrakt](../reference/stockout-training.md) podaje pełną składnię.
Natywne polecenia korzystały z `PYTHONPATH=src`, wheel z `PYTHONPATH`
wskazującym osobny katalog instalacji, przy tym samym istniejącym Python 3.11.15.

Pełny `make ci-local` przeszedł; koniec pomiaru: `2026-10-03T08:13:37.295049+00:00`.
**1835/1835 testów w 1555,79 s**, bez ostrzeżeń w pełnym przebiegu;
lint/format 568 plików, mypy 340 modułów, wszystkie 21 targetów `check`
i oba skany sekretów. Wheel z ponownej budowy ma identyczny hash
jak odebrany zainstalowany pakiet. Nowy commit wymaga osobnego zdalnego CI.
Poprzedni przyrost `aa6b953` ma zielone Required CI PR i push.

To ograniczony odbiór development. Cały AI 08 nie jest ready;
pełny profil i niezależna ocena, threshold policy, model card/importance
oraz lifecycle/batch/read API pozostają kolejnymi krokami.
