# Etykiety incident-stockout7d — pierwszy zakres AI 08

Pytanie modelu brzmi: czy produkt, który ma zapas w chwili `t`, osiągnie
zerowy dostępny zapas w następnych siedmiu dobach? Jednostką jest
`product_id × stock_location_id × as_of`. Kanały i sklepy korzystające ze
wspólnego magazynu nie tworzą osobnych etykiet tego samego fizycznego zapasu.
[Plan etapu](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/etapy/08-stockout-risk.md)
określa późniejsze cechy, modele, kalibrację, ocenę i serving.

## Znaczenie wyniku

| Status | Znaczenie | `incident_stockout` |
|---|---|---|
| `evaluable` | Pełne, dostępne okno obserwacji; zapas dodatni na początku | 1 albo 0 |
| `already_stockout` | Zapas już zerowy na początku | `null` |
| `not_evaluable` | Brak wystarczających dowodów lub kwalifikacji | `null` |

Nowy brak musi zacząć się w **`(t, t + 7 dni]`**. Zero po pojedynczym ruchu
liczy się także wtedy, gdy kolejny ruch uzupełnia zapas w tym samym czasie.
Kolejność określają `occurred_at` i `sequence`. Zdarzenie dokładnie w `t`
należy do stanu początkowego, a dokładnie na prawym końcu — do wyniku.
Etykieta dodatnia zachowuje czas i identyfikator pierwszego zdarzenia.

Obsługiwany kontrakt AI 06 używa `available_qty`, polityki rezerwacji `none`
i `available_qty = on_hand`, `reserved_qty = 0`. Inna semantyka rezerwacji
wymaga nowej wersji; nie zostaje domyślnie przyjęta. Brak danych o stanie
zapasów nie staje się zerowym zapasem.

## Wiedza w czasie i kompletność

Stan wejściowy musi być dostępny w `as_of`, mieć najwyżej 24 godziny oraz
zgadzać się z ledgerem przy własnym cutoff. Ruch sprzed `as_of`, którego
dostępność przypada dopiero później, wyklucza historyczne okno. Wynik może
używać późniejszych zdarzeń wyłącznie jako **etykiety**, nigdy jako cechy.

Świadectwo historii zapasów musi obejmować opening stock oraz całe okno.
`covered_through_at` jest końcem wyłącznym: musi być późniejszy niż
`t + 7 dni`. Niepełny ogon, brak pokrycia lub nieaktywność oznaczają
`not_evaluable`, nawet gdy w dostępnej części nie zaobserwowano braku.

`label_available_at` uwzględnia koniec okna, opóźnienie truth, dostępność
zdarzeń i dowodów kwalifikacji. `LabelPoint.eligible_at(training_cutoff)`
dopuszcza tylko dojrzałą etykietę dostępną przed dopasowaniem modelu.
Ten helper nie zastępuje jeszcze temporalnego splitu lub purgingu.
Wszystkie zegary są UTC. Adapter zachowuje dobowe origin AI 06
`23:59:59.999999`; nie zamienia ich na origin prognoz AI 04.

## Granica niezależnej kontroli

Budowa wymaga prywatnego [snapshotu inventory 1.1](inventory-snapshot-11.md)
z jawną zgodą na odczyt evaluation truth. Importer weryfikuje kontrakty,
identyfikatory, zawartość i uzgodnienie ledger/snapshots. Adapter czyta
przypięte `inventory_qualified_windows.json` z kwalifikacji producenta AI 06.
Kwalifikacja lifecycle, assortment, routing i sales coverage pozostaje
odpowiedzialnością tego zweryfikowanego dowodu producenta.

AI 08 niezależnie odtwarza ruchy magazynowe, sprawdza stan początkowy,
pokrycie historii, dojrzałość i nowy brak. Porównuje status oraz etykietę z
kwalifikacją AI 06; dla okien evaluable porównuje też dostępność etykiety.
Niezgodność zatrzymuje budowę. Nie ma tu niezależnego ponownego wyznaczania
wszystkich reguł lifecycle i sales coverage. Symulowana latent demand nie
jest wejściem algorytmu etykiet.

## Użycie offline

Zainstaluj pakiet z zależnościami snapshot. Przygotuj istniejący prywatny
katalog wyjściowy. Następnie:

```sh
python -m retailops_ai.stockout.cli build \
  --source /path/to/private-inventory-snapshot \
  --output /path/to/private-output/stockout-labels.json \
  --allow-evaluation-truth

python -m retailops_ai.stockout.cli verify \
  --source /path/to/private-inventory-snapshot \
  --output /path/to/private-output/stockout-labels.json \
  --allow-evaluation-truth
```

Wynik ma `role=stockout_labels`, `data_class=labels`, przypięte source,
snapshot i qualification IDs, politykę, hashe kodu konsumenta, lock zależności
oraz wersję Python. Identyfikator wiąże także pełną zawartość etykiet.
`verify` ponownie weryfikuje źródło i odtwarza cały wynik; samo przeliczenie
hasha zmodyfikowanej etykiety nie pozwala jej zaakceptować.
Odtworzenie wcześniej zapisanego wyniku wymaga tej samej przypiętej wersji
kodu, locka i Python; nowszy pakiet tworzy nową tożsamość artefaktu.

Zapis jest atomowy, prywatny (`0600`) i nie nadpisuje innego wyniku.
Powtórzenie tej samej treści zwraca `reused`. Symlinki i ścieżki z `..` są
odrzucane. CLI wykonuje tylko operacje plikowe offline.

Pierwszy zakres ma jawne limity: snapshot do 64 MiB i 500 000 wierszy,
ledger do 100 000 ruchów, kwalifikacja do 10 000 okien i wynik do 4 MiB.
Nie stanowi deklaracji wydajności pełnego profilu treningowego.
[Odbiór lokalny](../evidence/08-01-stockout-labels.md) dotyczy małego fixture.

## Pozostały zakres AI 08

Do wykonania są features dostępne w czasie, historyczne prognozy bez leakage,
temporalne train/tune/calibration/test, baseline LR i kandydat HGB,
kalibracja, progi według pojemności obsługi, metryki jakości oraz integracja
registry/batch/read API. Artefakt etykiet jawnie zachowuje `model_ready=false`.
