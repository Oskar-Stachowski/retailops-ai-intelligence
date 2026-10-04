# Etykiety pięciu ról development

AI 09.8 dodaje fizyczny czytnik prognoz dla `train`, `early_stopping`, `tune`,
`calibration` i diagnostycznego `development_evaluation`. Każdy odczyt ma
rezerwację w [trwałym dzienniku](outcome-access-journal.md) przed otwarciem
jakiegokolwiek wejścia. To kolejny element przygotowania; cały AI 09 jest
`in_progress / not_ready`.

## Dowód i granica kwalifikacji

Jedna rola ma dokładnie dwa pliki: kanoniczny `manifest.json` i uporządkowany
`outcomes.jsonl`. Zewnętrznie zamrożony manifest przypina pełne źródło,
snapshot, curated, seed danych, cechy, podział, rolę, cutoff, hash wszystkich
kluczy, liczbę wierszy i hash fizycznego pliku wyników. Każdy klucz ma jawne
wersje obserwacji dla swojego produktu, selling location, kanału i target date.
Pusty zestaw wersji zachowuje brak etykiety. Nadmiar, brak, duplikat, zmiana
kolejności lub niezgodne źródło powodują błąd; czytnik nie używa przecięcia
populacji ani limitu obcinającego wiersze.

Hash potwierdza odebrane bajty, a nie prawdziwość obserwacji. Manifest jawnie
ma `source_qualification=not_established`: pełne curated/source nie są tutaj
odtwarzane. Potrzebny jest jeszcze audytowany eksporter, który porówna dowody
z rzeczywistym, przypiętym źródłem. Nie wolno przedstawiać tego czytnika jako
samodzielnego odbioru źródła, wyników modelu lub gotowej kampanii.

Czytnik odtwarza cały publiczny feature/history parent. Historyczne obserwacje
mogą ujawniać wyniki innych ról; wersje po cutoff obecne w dowodzie także są
fizycznie odczytane, choć nie są wybrane do etykiety. Protokół zapisuje oba
zakresy ekspozycji. Częściowy dziennik nie dowodzi świeżości żadnego holdoutu.
`independent_evaluation` jest odrzucane przed otwarciem dziennika i danych.
`verification` w development evaluation pozostaje wyłącznie diagnostyką.
Portfolio final test nie występuje w tym formacie.

## Wersje, dojrzałość i eligibility

Wybierana jest najwyższa jednoznaczna wersja z rzeczywistym
`curated_available_at <= label_knowledge_cutoff`. Wspólna funkcja wyboru
pochodzi z istniejących forecast features. Nie ma powrotu do starszej
kompletnej wersji, gdy nowsza znana wersja jest niekompletna. Sprzeczne payloady
lub zestawy wersji tego samego targetu w różnych origin są odrzucane.

Etykieta jest dojrzała dopiero od końca target date powiększonej o jawny
`label_delay_days`: cutoff musi przypadać po tym czasie. Availability musi
przypadać od końca target date do cutoff; wcześniejszy kompletny delivery
pozostaje użyteczny po dojrzeniu, bez wymagania sztucznie późnej dostawy.
Potrzebuje też kompletnego źródła,
statusu jakości `valid` i jawnej ilości. Potwierdzone zero oraz zamknięty dzień
mają ilość 0; brak, zbyt wczesny zapis i niekompletne źródło pozostają
`censored` z `observed_sales_units=null`. Wybrana wersja jest zachowana jako
lineage również przy censoring, więc sama jej surowa ilość nie jest targetem
do treningu.

Eligibility zachowuje reguły resolved feature policy: minimum historii,
minimum znanych dni, maksymalny wiek obserwacji oraz znany otwarty kalendarz.
Zamknięty target pozostaje w pokryciu, ale poza scoringiem. Każdy wiersz ma
powody odrzucenia; label eligibility i scoring eligibility są osobne.

## Użycie i audyt

`ForecastOutcomeReadProtocol` przypina manifest podziału, 1–5 manifestów
dowodów i limity. Jego hash musi być dokładnym `protocol_sha256` planu
dostępu. Każdy binding zachowuje właściwą parę rola/cel, osobny seed
inicjalizacji modelu i hashe recipe/candidate/calibrator/thresholds.
`calibrator_fit` wymaga deklarowanego hasha kandydata; ten czytnik nie
sprawdza jeszcze jego artefaktu, fitu ani zgodności recipe. Te kontrole
należą do przyszłej integracji protokołu treningu. Zmiana protokołu lub kodu
wymaga nowego planu w tym samym dzienniku, zachowującego historię i limity.
Czytnik nie tworzy dziennika i nie rejestruje planu automatycznie.

```python
with open_forecast_outcomes(
    features,
    partitions,
    evidence_root,
    protocol,
    journal=journal_root,
    plan_sha256=plan_sha256,
    binding=binding,
) as reader:
    for outcome in reader.rows():
        consume(outcome)
```

Cała walidacja odbywa się przed udostępnieniem prywatnego snapshotu.
Iterator przestaje działać po wyjściu z kontekstu, a baza i jej uchwyty są
zamykane także po błędzie. Błędy konsumenta,
zmienione pliki i SIGKILL pozostają naliczone jako failed lub unresolved.
Nie ma trwałego cache przyjmowanego jako wynik wcześniejszej weryfikacji.
Powtórzenie CLI także jest nowym odczytem, zużywającym budżet.

```bash
python -m retailops_ai.evaluation_campaign.label_cli \
  --features /abs/features --partitions /abs/partitions \
  --evidence /abs/role-evidence --protocol /abs/read-protocol.json \
  --expected-protocol-sha256 "$PROTOCOL_SHA256" \
  --binding /abs/binding.json --journal /abs/shared-journal \
  --access-plan-sha256 "$ACCESS_PLAN_SHA256"
```

CLI zwraca liczniki i hash kwalifikacji, bez wierszy targetów. Exit 0 oznacza
spójny diagnostyczny odczyt, nadal z `evaluation_status=not_ready`; exit 2
oznacza odmowę lub błąd. Wartości środowiska powyżej są jawnie zamrożonymi
hashami protokołu oraz planu, nie zgodą na niezależną ocenę.

## Zasoby i dalszy zakres

Odczyt przenosi dowód na prywatny dysk, sprawdza hash, a następnie buduje
query-only SQLite. Nie materializuje całej roli w pamięci. Używa istniejących
forecast Parquet/history readers oraz cache SQLite 4 MiB; nie kopiuje
stockout readera o innym fizycznym grain. Jedno wywołanie istniejącego `verify_partitions` sprawdza cały podział i
wewnętrznie weryfikuje feature parent także po replay. Dodatkowe seals
sprawdzają bajty przed udostępnieniem i przy zamknięciu; nie jest to odbiór
minimalnej liczby replay na większym profilu.

Limity: 100k wierszy, 64 MiB dowodu, 32 KiB na rekord i do 8 wersji na klucz,
128 MiB bazy oraz do 4096 plików / 256 MiB na publiczny parent. Przekroczenie
odrzuca kompletny odczyt. To ograniczenia mechanizmu; większy profil nie jest
odebrany samym istnieniem limitu. Pomiary i dokładny zakres kontrolnego
fixture podaje [odbiór 09.8](evidence/09-08-forecast-outcome-reader.md).

Pozostają: audytowany eksport i pełny source replay, szerszy audyt raw/curated/
history, rzeczywiste świeże okna danych, połączenie pięciu ról z rejestrem
fitów, kalibracja i niezależna ocena, większy profil, uncertainty/robustness
oraz końcowa integracja AI 07/08. Dotychczasowy comparator i jego historyczne
etykiety nie zostały automatycznie przepięte na ten czytnik.
