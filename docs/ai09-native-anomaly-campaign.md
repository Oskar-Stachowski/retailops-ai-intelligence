# Natywny scoring anomaly w kampanii AI 09

AI 09 pozostaje `in_progress / not_ready`. Ten komponent przygotowuje wykonanie
pełnej kampanii; nie jest dowodem jakości modeli ani zakończenia etapu.

`campaign_anomaly_scoring.iter_anomaly_census_scores` przetwarza pełny, jawnie
zadany census series/day w partiach. Oryginalny scorer AI 07 nadal przyjmuje
najwyżej 10 000 punktów na jedno wywołanie. Nowy iterator dzieli wyłącznie
wykonanie: nie przycina zakresu dat, liczby serii ani brakujących deklaracji.
Domyślnie partia mieści najwyżej 8192 punkty, z uwzględnieniem sześciu dni
historii. Każda seria i jej historia pozostają w tej samej partii. Zachowuje to
oryginalne cechy multiscale/count-rate i ich zegar wiedzy.

Obie rodziny, `seasonal_residual` i `isolation_forest`, korzystają z istniejącego
modelu, scorerów i progów. Każda partia jest niezależnie odtwarzana przez
`anomaly_evaluation.verification.verify_scores` przed zwróceniem decyzji.
Wynik zawiera decyzję, jej publiczny model row i hash odpowiedniego publicznego
Point. Brak Point pozostaje abstention, bez score, alertu ani fałszywego zera.
Nieznana lub niedojrzała prawda nie jest przez ten komponent klasyfikowana.

Wejście musi być uporządkowane według natywnego klucza serii i daty, bez
duplikatów, obcych serii, `null` ani dat spoza jawnego okna i jego historii.
Nieprawidłowy budżet odrzuca cały census przed odczytem cech. Limity metadanych
wynoszą 65 536 serii i 20 000 000 żądanych decyzji; nie zwiększają limitu
pojedynczego wywołania natywnego scorera. Oryginalne warunki train/selection
cutoff, roli validation i dostępności obserwacji pozostają egzekwowane przez
natywny scorer. Konsument musi wyczerpać iterator: późniejszy błąd parenta
unieważnia operację, a wcześniejsza partia nie kwalifikuje częściowego wyniku.

## Odbiór komponentu

20 nowych kontroli i 16 istniejących regresji przeszło razem: **36 passed**.
Kontrole porównują wynik z rzeczywistym natywnym scorerem obu rodzin, także
z modelami multiscale/count-rate i sześciodniową historią. Zapisany mały Isolation Forest
jest rzeczywistym modelem kontrolnym. Test 10 400 żądanych decyzji przekracza
oryginalny limit pojedynczego wywołania i sprawdza pełny census, wszystkie
abstentions i ograniczenie wielkości każdej partii. Sprawdzono także błędy
strumienia, granice budżetów, zachowane cutoffy i odrzucenie przez niezależny
replay. Te kontrole nie wykonują projektowych fitów ani odczytów świeżego final.
Pełna konfiguracja Mypy zaliczyła 719 plików; Ruff zaliczył repozytorium.
Te same 36 kontroli przeszło z oddzielnie zainstalowanego wheela, po sprawdzeniu
rzeczywistej ścieżki importu. Wszystkie 598 modułów Python i 213 plików JSON
w paczce są bajtowo identyczne z odpowiednimi plikami źródłowymi i kontraktami
wskazanymi przez konfigurację pakowania.

## Pozostała integracja

[Bramka kwalifikacji AI 09](ai09-native-selection-verification.md) odrzuca
receipts anomaly/stockout bez pełnej natywnej weryfikacji Project. Sam scorer
i poprawne hashe artefaktów nie otwierają projektowych danych final.

Przed projektową kampanią należy powiązać iterator z odtwarzalnym, kompletnym
publicznym feature parentem, zarejestrowaną operacją dziennika, pełnym portfolio
źródeł oraz trwałym artefaktem i niezależnym verifierem. Ocena musi użyć
natywnych semantyk observation/episode, tolerance, dedup, false alerts/1000,
unknown truth i przypadków bez pozytywów, a także pełnych wymaganych grup,
porównania rodzin, sparowanej niepewności i rzeczywistych kosztów.

Istniejąca bramka jakości AI 07 wymaga własnych sześciu finalnych przypadków
42/137/2026 × demand/physical. Nie stanowi dowodu oceny development AI 09
ani jego wariantu ordinary. Oryginalny zakres AI 09 obejmuje trzy zastosowania
i 12 pełnych źródeł; TensorFlow challenger dotyczy forecastingu. Ten scorer
nie czyta Source, truth ani pozwolenia final, nie trenuje, nie inicjalizuje
dziennika i nie zmienia lifecycle. Autoryzacja i odbiór całej operacji należą
do adaptera kampanii. Wcześniejsze AI 07–08 pozostają READY i zamknięte.
