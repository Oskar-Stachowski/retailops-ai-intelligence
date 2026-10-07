# AI 09.6 — odbiór przygotowania pięciu ról prognoz

[Opis i polecenia](../independent-forecast-partitions.md) oraz
[wersjonowany receipt](09-06-independent-forecast-partitions.json)
wiążą przygotowanie podziału z kontrolnymi dowodami. AI 09 pozostaje
`in_progress / not_ready`; nie wykonano nowego fitu na danych projektu.

## Wykonane kontrole

- 52 testy nowego podziału; 154 testy razem z przygotowaniem AI 09,
  rejestrem prób, adapterem pamięci i dotychczasowym porównaniem.
- Pełny lint/format: 579 plików; mypy: 346 plików źródłowych.
  Schematy kontraktów przygotowania, rejestru i nowego podziału zgodne.
- Rzeczywiste typed Parquet publicznego, kontrolnego fixture'u: 65 originów,
  **865 kluczy**, w tym train / early stopping / tune / calibration po 14,
  development evaluation 5, purged 804. Końcowy asortyment ma krótsze
  horyzonty; wszystkie istniejące klucze zachowano dokładnie raz.
- Weryfikacja nie przyjmuje plików etykiet. Test integracyjny zastępuje
  istniejący split kontrolnego fixture'u nieczytelnymi etykietami, a
  nowe przygotowanie i verify nadal działają. Nie dotyczy to danych projektu.
- Odrzucono nakładające się okna, za krótki purge, niedojrzałe i przyszłe
  cutoffy, pomyloną rolę, nieuprawnioną ocenę, duplikaty, podmienione
  przypisania także po reseal, uszkodzenie, dodatkowe pliki, symlinki,
  zmianę cech/kodu podczas przygotowania i przekroczenie budżetu.
- Natywny build/verify/preflight oraz zainstalowany wheel verify/build
  poza checkoutem odtwarzają identyczne bajty manifestu. **271 modułów
  Python** wheel ma identyczne bajty jak source; trzy nowe schematy
  przechodzą walidację. Odczyt calibration zwraca 14 kluczy.
- Żądanie development evaluation blokuje się przed odczytem wejść.
  TensorFlow/Keras nie są importowane. Uszkodzona kopia artefaktu daje
  exit 2, poprawny oryginał pozostaje nienaruszony.
- Szablon dla rzeczywistego 60-dniowego okna dotychczasowego projektu
  daje exit 2 nawet przy rolach jednodniowych, bez odczytu jego etykiet.
- Z samych dotychczasowych protokołów i receiptów przygotowano deklarację
  wcześniejszego dostępu: 11 prób, 10 protokołów, jeden source i split.
  Ich development holdout był odczytany podczas weryfikacji rodziców;
  nie staje się nietknięty po zmianie katalogu. To retrospektywna
  inwentaryzacja do przyszłego audytu, nie zapis wykonany przed odczytem.
  Oryginały pozostają niezmienione; żadnych nowych etykiet nie odczytano.

## Pomiary małego fixture'u

Dziewięć świeżych procesów miało z góry limit 300 sekund / 1024 MiB
całego drzewa. Native prepare+verify: 7.51 s / 121.14 MiB; wheel
prepare+verify: 7.30 s / 125.98 MiB. Sam verify: native 3.79 s /
121.50 MiB, wheel 3.97 s / 123.16 MiB. Odczyt roli i kontrola schematów
w wheel: 7.81 s / 120.52 MiB. Największy zaobserwowany scratch wyniósł
996 704 bajty. Pomiar RSS/CPU/scratch próbkowano co 50 ms;
przyrosty pomiędzy próbkami mogą być niewidoczne. CPU jest obserwowanym
kosztem procesów, nie dokładnym całkowitym rozliczeniem zakończonych dzieci.

Artefakt ma 341 126 bajtów, cechy rodzica pozostają bajtowo identyczne.
To odbiór mechaniki na małej populacji, nie pomiar większego profilu
ani całej przyszłej kampanii. Surowe stdout/stderr, modelowe dane,
pakiet i kopie kontrolne pozostają poza Git.

## CI i granice

Pełne lokalne `make ci-local` przeszło: **1914 testów głównych** w 2235.93 s
i **3 wymagane rzeczywiste testy TensorFlow CPU** w 76.10 s, bez pominięć.
Wszystkie bramki check, pakiet, Compose config i oba skany sekretów przeszły.
Całość trwała 2534.88 s przy nice 15 i jednym wątku bibliotek numerycznych;
790 zamrożonych plików kodu/konfiguracji/kontraktów nie zmieniło się.
Własny katalog pytest ogranicza współdzielenie plików tymczasowych.
Required CI 152 poprzedniego head 4d2f773 zakończyło się sukcesem. Nowy
commit wymaga własnego Required CI i nie dziedziczy tego wyniku.

Nie kwalifikowano kompletności etykiet, jakości modelu ani świeżości
holdoutu. Dotychczasowy benchmark nadal używa własnego, diagnostycznego
splitu. Podłączenie nowego podziału do treningu, audyt dostępu do wyników,
kalibracja, większa skala i końcowe AI 07–08 pozostają wymagane.
AI 08 ma już bounded upstream partitions i temporal join; jego moduły,
worktree i usługi nie były zmieniane przez ten przyrost.
