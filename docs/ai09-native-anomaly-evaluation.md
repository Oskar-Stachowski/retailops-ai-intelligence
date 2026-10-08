# Natywna ocena anomaly w AI 09

AI 09 pozostaje `in_progress / not_ready`. Komponent
`campaign_anomaly_evaluation.evaluate_anomaly_census` podłącza pełny zadany
census decyzji do rzeczywistych metryk AI 07 i niezależnego replay zapisanego
modelu. Nie otwiera źródeł, nie trenuje i nie nadaje pozwolenia na dostęp do final.

Plan przypina model, publiczny feature parent, źródło, scenariusz i offline
truth, rodzinę modelu, jawne serie, okno, cutoff i oryginalną politykę metryk.
Development używa natywnej roli `batch` po selection cutoff; `validation`
należy do wcześniejszego wyboru progów. Final używa `final_test`.
Przed konsumpcją decyzji komponent sprawdza budżet całego census i zgodność
modelu oraz prawdy. Brakująca, nadmiarowa, powtórzona, obca lub nieuporządkowana
decyzja odrzuca ocenę. Brak Point pozostaje natywną abstention.

Wszystkie zapisane score i model rows są odtwarzane przez
`anomaly_evaluation.verification.verify_scores` w partiach do 8192 decyzji.
Następnie niezmieniony `anomaly_evaluation.evaluator.evaluate` liczy metryki
observation i episode, tolerancję, powtórzone alerty, opóźnienia, false
alerts/1000, liczby obserwacji dodatnich i czystych, segmenty event/currency
i rodzaje epizodów.
Nieznana i niedojrzała prawda oraz niewystarczające dane zachowują odrębne
liczniki i właściwe mianowniki. No-positive i brak predykcji nie dają
idealnych wartości; wymagane metryki pozostają `not_evaluable` lub `null`.

Verifier ponownie odtwarza całą ocenę względem niezależnie dostarczonego planu
i parentów. Ponowne przeliczenie hashów zmienionego raportu lub jego planu
nie wystarcza do odbioru. Wynik zawsze zachowuje `parent_source_verified`,
`critical_segment_inventory_complete`, `block_uncertainty_complete`,
`quality_qualified`, `promotion_allowed` i `stage_ready` jako false.
Hashe są wiązaniami dla nadrzędnego adaptera, a nie dowodem weryfikacji
publicznego feature parenta przez ten komponent.

Oryginalny limit natywnej oceny, 1 000 000 decyzji, pozostaje zachowany.
Za duży zadeklarowany census kończy się błędem przed odczytem decyzji;
nie jest obcinany ani pomniejszany. Ocena zachowuje pełną listę decyzji na
potrzeby natywnego evaluatora. Kontrola 10 408 abstentions potwierdza przejście
przez więcej niż jedną partię replay, ale nie dowodzi pełnej pojemności danych
Project ani limitu pamięci dla największego rzeczywistego przypadku.

Do pełnego odbioru należy powiązać komponent ze zweryfikowanym publicznym
feature parentem, niezależnym truth producerem dla wszystkich wariantów,
operacją i kosztami w dzienniku, zapisanym artefaktem, wymaganymi grupami
oraz sparowaną niepewnością. [Bramka kwalifikacji](ai09-native-selection-verification.md)
pozostaje zamknięta. Stockout wymaga analogicznej rzeczywistej integracji;
pełny zakres obejmuje 12 źródeł i trzy zastosowania.

[Dowód 09.56](evidence/09-56-native-anomaly-evaluation.json) zapisuje lokalny
odbiór na jawnych fixture: 25 nowych kontroli, 81 łącznie z regresjami i
25 z osobno zainstalowanego wheela. Mały Isolation Forest jest rzeczywistym
modelem kontrolnym. Pełne CI i publikacja na main pozostają wymagane.
