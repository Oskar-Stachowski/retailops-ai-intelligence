# Kwalifikacja dnia raw-DQ na dysku w AI 09

`CampaignAnomalyDiskDayGate` łączy [pełną projekcję dni](ai09-native-anomaly-days.md)
z [pełnym replay capture](ai09-native-anomaly-replay.md) tego samego publicznego
parenta. Plan przypina oba plany, hash wszystkich dni oraz dokładną liczbę
zaakceptowanych faktów i rekordów kwarantanny. Brak lub nadmiar danych
uniemożliwia otwarcie bramki.

Bramka używa niezmienionego `DayGate.point`. Wszystkie dni i zaakceptowane
fakty pozostają na dysku. Lookup używa pełnego GRAIN i natywnego klucza
biznesowego; fakty zachowują rzeczywistą chwilę odbioru. Przed dostępnością
deklaracji lub wymaganych faktów dzień nie może dostarczyć wartości.
Brak deklaracji, niepełne źródło, zamknięta lokalizacja i nieprzypisana
kwarantanna zachowują oryginalne statusy. Znane zero wymaga tych samych
natywnych warunków co wcześniej.

Replay zachowuje pełny sprawdzony capture każdego odrzuconego rekordu.
Bramka przekazuje po jednym takim rekordzie do oryginalnego konstruktora
`DayGate`, przy globalnych indeksach parenta. Najwcześniejsza chwila
nieprzypisanej kwarantanny wystarcza do dokładnego odtworzenia natywnego
warunku `any(received_at <= as_of)`. Nie powstaje pełna kopia capture,
faktów ani listy wszystkich nieprzypisanych rekordów w pamięci.

Read-only mapy parenta zachowują także semantykę błędnych kluczy słownika:
lista lub obiekt jako identyfikator powoduje TypeError, który natywna
klasyfikacja traktuje jako nieprzypisaną kwarantannę. Inne niepasujące klucze
pozostają nieobecne. Błąd prywatnego stanu ma typ RuntimeError i nie może
zostać ukryty jako zwykła kwarantanna zdarzenia.

Każdy zapis wyniku replay aktualizuje oddzielny hash strumienia w rzeczywistej
chwili przetwarzania. Zapisane wiersze mają własne hashe. Pełna kontrola przed
zakończeniem capture i przy wyjściu ponownie sprawdza receipts, fakty,
rewizje, progress, kwarantannę i odrzucone capture. Sprawdza również klucze,
GRAIN, dostępność i powiązanie body z odrzuconym rekordem. Ponowne
zapieczętowanie zmienionego wiersza nie zmienia pierwotnego hasha strumienia.

[Dowód 09.61](evidence/09-61-native-disk-day-gate.json) zapisuje 60 kontroli
pełnych publicznych źródeł ordinary/demand/physical i 60 kontroli z paczki,
bez pominięć. 43 dotychczasowe kontrole całego replay również przeszły.
Pełne CI i publikacja pozostają wymagane.

Wynik bramki wymaga późniejszego poprawnego zakończenia zewnętrznych
kontekstów dni, replay i publicznego parenta. Nie dowodzi genealogii
generation, polityki zamknięcia producenta ani rezerwacji odczytu w dzienniku.
Do pełnej kampanii pozostają causal Point census i cechy, niezależna prawda
ordinary/demand/physical, rzeczywiste powiązania artefaktów i kosztów,
grupy krytyczne oraz sparowana niepewność. Integracja stockout, pełne treningi,
TensorFlow, MLflow, lifecycle i końcowa ocena pozostają wymagane.
`quality_qualified` i `stage_ready` pozostają false.
