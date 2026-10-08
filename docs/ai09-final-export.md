# AI 09 — eksport wyłącznie końcowej oceny

`campaign_final_export.export_final_forecast` używa osobnego planu i formatu
v14. Zapisuje tylko `final_evaluation.jsonl` oraz komplet zweryfikowanych cech
i historii dla zamrożonych originów. Nie dodaje końcowych etykiet do pięciu
wcześniejszych ról development. Ich stare schematy pozostają bez zmian.

Przed parent I/O runner rezerwuje zaplanowany final `source_read`. Journal
blokuje tę próbę przed selection freeze i przed zakończeniem generacji.
Wewnątrz zarezerwowanej próby sprawdzane są digest planu, source recipe,
wersjonowane pełne parametry canonical 730 × 200 × 10 × 4, seed, originy,
label delay, runtime, pełny prywatny receipt zakończonej generacji oraz lock
producenta i osobny lock eksportera. Zmieniony plan lub receipt kończy próbę
porażką przed odczytem rodzica; budżet pozostaje zużyty.

Plan deklaruje wcześniejszą ekspozycję i wymaga późniejszych originów,
oddzielonych o horyzont 14 dni i label delay. Cutoff musi zapewniać dojrzałość
całego końcowego okna i mieścić się w rzeczywistej historii źródła. Sama
deklaracja dat nie dowodzi globalnej świeżości: projektowy protokół musi
uwzględnić wszystkie wcześniejsze odbiory, w tym kontrolne kohorty do
2026-09-30. Statyczne flagi planu, manifestu i receipt pozostają false.

Jeden pełny prywatny source/curated replay odbudowuje availability-aware
calendar i features oraz pełny indeks wersji etykiet. Wspólne reguły
kwalifikacji wybierają najnowszą wersję znaną na cutoff, zachowują braki i
cenzurowanie oraz nie uczą preprocessing na końcowych etykietach. Wewnętrzny
adapter istniejących czystych reguł kwalifikacji nie tworzy development
artefaktów ani uprawnień; zapisany format dopuszcza tylko final_evaluation.

Budowa i verifier używają limitowanego SQLite zamiast przechowywania całej
populacji w pamięci. Verifier sprawdza komplet i kolejność tych samych kluczy,
hashe cech, dojrzewanie/eligibility, coverage i pełny inventory artefaktu.
Samodzielny verifier bada spójność przechowanych bajtów, a nie prawdziwość
samodzielnie zadeklarowanego źródła. Projektowy odbiór opiera się na dokładnym
artefakcie zbudowanym w audytowanym replay i zakończonej operacji journal.

Guard niezmienności źródła i runtime na wyjściu z replay musi przejść przed
trwałym zapisaniem receipt. Receipt 0600 w prywatnym katalogu 0700 wiąże
selection freeze, wygenerowane rodzice, źródłowy plan, cały runtime, manifest
i koszt wall/artifact bytes. Dopiero później journal otrzymuje completion.
Walidacja lokalnego completion nie przyznaje nowego odczytu lub treningu.
Peak całego drzewa tego eksportu nie został jeszcze zmierzony; nie jest zero.

Mały native control na wcześniej używanym ai-smoke 30-dniowym źródle
odbudował i zweryfikował final wire w 40.49 s. To kontrola komponentu:
nie używa canonical profilu, nie kwalifikuje świeżości holdout i nie jest
końcową kampanią. Pierwszy odbiór ordering miał 5 failed/7 passed: cztery
oczekiwania typu błędu budżetu i jedno oczekiwanie typu błędu bind były błędne.
Kolejny miał 1 failed/15 passed i ujawnił brak mapowania nieobecnego receipt
generacji na publiczny SnapshotError; runner został poprawiony. Porażki są
zachowane. Wspólna regresja zaliczyła 176 testów w 231.82 s. Dodatkowy native odbiór
trzech samodzielnie resealowanych uszkodzeń oraz checksum ma 1 passed
w 139.07 s. Ruff/format, Mypy 619 plików, contracts i docs są zielone.
Zbudowany wheel zachowuje identyczne bajty 506 modułów i pięciu schematów v14.
Wyniki są zapisane w
[receipt](evidence/09-21-final-export-preparation.json).

W tej samej publikacji receptura capacity 1.2 wykorzystuje istniejącą szybką
ścieżkę AI08. Przed pierwszym dispatch przypina source PR103 `16d34887`;
stare plany 1.0/1.1 i porażki są zachowane. Historyczny tiny control na
`5bec26f` pozostaje historycznym dowodem połączeń. Nowy canonical pomiar
wymaga pełnego CI, chronionego merge producenta i odbioru jego main.

Projektowy journal nie jest zainicjalizowany, liczba nowych fitów wynosi zero,
final generation/test pozostają nieotwarte. Trzy zastosowania, trzy końcowe
seedy, fair trening/kalibracja/freeze, robustness/segmenty/niepewność/koszty,
MLflow/lifecycle, karty/raporty oraz pełne CI/main pozostają warunkami AI09 ready.

Przed publiczną generacją lub eksportem końcowych danych wymagane są teraz
również `selection_bundles` dla forecast, anomaly i stockout. Wspólny
`verify_completed_campaign_selection` sprawdza trwałe dowody ukończonej
niezależnej oceny development, zgodność zamrożonych rodziców, segmentów
i niepewności oraz artefakty. Jest wykonywany wewnątrz rezerwacji, przed
producer inspection lub otwarciem końcowego source/curated. Generic journal
freeze sam audytuje kolejność i nie zastępuje tych dowodów. Brak dowodu
kończy zarezerwowaną próbę porażką bez dostępu do końcowych danych.
Aktualny forecast component z nieukończonymi segmentami/niepewnością nie może
autoryzować takiego dostępu. [Evidence kontroli](evidence/09-31-independent-forecast-components.json)
oddziela rzeczywisty verifier od jawnie mockowanych kontroli publikacji.
