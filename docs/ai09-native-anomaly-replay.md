# Natywny replay capture anomaly w AI 09

Globalny indeks `event_hashes` ma teraz niezależny hash liczony w chwili
przyjęcia każdego nowego ID oraz jawną kolejność wpisów. Ukończenie i zamknięcie
replay weryfikują jego pełną zawartość i liczbę, razem z dotychczasowymi wynikami.
Zmiana digestu, usunięcie, dopisanie albo zmiana kolejności blokują odbiór także
wtedy, gdy receipts i fakty pozostały bez zmian. Natywny kernel nie nadpisuje
przyjętego ID; nieoczekiwana próba nadpisania kończy cały replay błędem.
Reguły duplicate event, content conflict i duplicate business pozostają natywne.

`campaign_anomaly_replay.CampaignAnomalyDiskReplay` przenosi globalny stan
operacyjnego replay na dysk. Używa niezmienionego `full_raw_dq.replay.Replay._event`
do sprawdzania zdarzeń i obliczania rewizji agregatów. Cały capture zachowuje
globalne offsety, tożsamość rekordów, tożsamość zdarzeń i faktów biznesowych,
kwarantannę, jawne frontier oraz dostępność danych w czasie dostarczenia.

SQLite przechowuje receipts, fakty, rewizje, deklaracje postępu i kwarantannę.
W pamięci pozostaje poprzedni stan jednej grupy agregacji i bieżące zdarzenie.
Transakcje obejmują domyślnie 256 rekordów; wcześniejsze zapisy tej samej
transakcji są widoczne dla kolejnych kontroli duplikatów. Błąd przerywa
operację i blokuje odczyt częściowych wyników. Nie oznacza kwarantanny
poprawnego zdarzenia. Każdy rekord jest wliczany do hasha i długości capture,
także ponowne dostarczenie tego samego rekordu.

Plan przypina SHA-256 całego canonical JSONL, liczbę rekordów i pełną liczbę
parent facts. Wyniki można czytać po zgodnym zakończeniu całości. Limit dysku
uwzględnia rozmiar logiczny i przydzielone bloki bazy oraz dodatkowe pliki,
w tym rollback journal. Liczy także brudne strony przed commit. Odrębne
limity liczby i bajtów grupy obejmują również bieżący przyjęty fakt.
Przekroczenie limitu kończy operację błędem; nie obcina capture ani grupy.
Własny katalog tymczasowy jest usuwany po zamknięciu.

Jawna wersja `ai09-project-raw-dq-capture-1.0.0` pozwala rejestrować pełny
strumień Project z globalnymi offsetami powyżej limitów dawnego capture.
Zachowuje oryginalny schemat zdarzeń, walidację wire, reguły duplikatów,
kanoniczną tożsamość, budżet body i reguły czasu. Stara wersja capture 2.0
nadal ma limit 8192 offsetów i 16384 rekordów. Kontrakty i odbiory AI 07
nie zostały zmienione ani przemianowane.

Komponent przyjmuje `ParentFacts` dostarczony przez nadrzędny adapter.
Nie weryfikuje jego publicznej genealogii Source i nie ogranicza pamięci
implementacji tego parenta. Do rzeczywistej kampanii nadal potrzebny jest
zweryfikowany, ograniczony adapter pełnych Source/snapshot 1.1/curated,
rekonstrukcja publicznych parentów, kompletny day qualification i Point census,
niezależny offline truth, rezerwacja operacji w dzienniku oraz koszty i artefakty.
Replay nie dowodzi kompletności business day ani trwałości transportu.
Wynik zachowuje `source_parent_verified`, `quality_qualified` i `stage_ready`
jako false. AI 09 pozostaje `in_progress / not_ready`.

[Dowód 09.57](evidence/09-57-native-anomaly-disk-replay.json) zapisuje odbiór
194 kontroli z regresjami oraz 43 z osobno zainstalowanego wheela, bez
błędów i pominięć. Porównuje pełne rzeczywiste capture demand/physical,
obie wielkości transakcji, globalny strumień 20 000 rekordów i błędy zasobów.
Mypy sprawdził 721 plików; paczka zachowuje identyczne 600 modułów Python
i 213 plików JSON. Pierwsze 40 zaliczonych i dwa nieudane testy zachowano:
poprawka normalizuje wyjątek dla błędnego cutoff UTC. Pełne CI oraz publikacja
na `origin/main` pozostają wymagane.
