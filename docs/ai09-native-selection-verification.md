# Natywna weryfikacja kwalifikacji AI 09

AI 09 pozostaje `in_progress / not_ready`. Limit drzewa RSS diagnostyki wynosi
12 GiB. Ta poprawka dotyczy dostępu do projektowych danych final, niezależnie
od przygotowanej diagnostyki zasobów 1.9.

Audyt `verify_completed_campaign_selection` wykazał, że dla anomaly i stockout
sprawdzenie kompletności dziennika, wybranych komponentów, kosztów, plików
i ich hashów nie odtwarzało naukowej oceny. Receipt mógł sam zadeklarować
`quality_qualified`, kompletne grupy oraz niepewność. Poprawne hashe potwierdzają
integralność takiej deklaracji, ale nie potwierdzają jej wyniku.

Verifier nadal wykonuje wszystkie wcześniejsze kontrole, a po sprawdzeniu
artefaktów anomaly/stockout odrzuca kwalifikację kodem
`campaign_evaluation_<use_case>_native_verification_unavailable`.
Publiczne operacje wymagające ukończonej kwalifikacji trzech zastosowań
pozostają zamknięte przed odczytem final. Weryfikacja forecast nadal używa
istniejącego typowanego receipt i rzeczywistego verifiera. Dziennik i zapisany
hash freeze nie zmieniają się podczas odrzuconej weryfikacji.

Odblokowanie wymaga implementacji pełnej projektowej oceny i niezależnego
verifiera anomaly oraz stockout: właściwych parentów i źródeł, kompletnego
population census, natywnych metryk, wymaganych grup i sparowanej niepewności.
Nie można uzyskać tego dowodu przez przemianowanie wyników AI 07–08; ich
zamknięte protokoły i dane mają własne warunki. Natywny iterator anomaly
pozostaje komponentem scoringu, a nie kwalifikacją całego etapu.

Kontrole granicy dziennika używają jawnych dubli naukowych, aby sprawdzać
kolejność, integralność i zgodność wybranych komponentów osobno. Dodatkowe
kontrole produkcyjnej blokady nie zastępują native verification: sprawdzają
oba zastosowania w starym protokole i pełnym portfolio. Nawet spójne hashe,
przesłanki, koszty i freeze nie otwierają final na podstawie samych flag.
Kontrole używają tymczasowych fixture; nie inicjalizują dziennika Project,
nie wykonują nowych projektowych fitów i nie odczytują świeżego final.

[Dowód 09.55](evidence/09-55-native-selection-verification-guard.json) zapisuje
81 zaliczonych kontroli i sześć z zainstalowanego wheela, 719 plików Mypy,
Ruff/format i kontrole dokumentacji. Wszystkie 598 modułów Python i 213 plików
JSON paczki są bajtowo zgodne z konfiguracją pakowania; prywatnych plików
w niej nie ma. Zachowano nieudane pierwsze kontrole i ich przyczyny.
To przygotowana lokalnie poprawka. Publikacja oraz pełne CI dokładnego head
i wynikowego main pozostają wymagane; nie jest to odbiór jakości modeli.
