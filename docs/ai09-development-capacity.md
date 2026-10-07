# Pomiar pełnego development AI 09

[Plan](reference/ai09-development-capacity.json) określa prawdziwe `ai-dev`:
365 dni, 100 produktów, 5 sklepów, 3 lokalizacje zapasu i seed 42. Generator
jest przypięty do `1de4627`, historia kończy się 2026-07-31, a znane plany mają
14 dni. To diagnostyka zasobów na wcześniej eksponowanych datach development.
Nie jest źródłem prospektywnej kampanii ani dowodem świeżości final test.

Workflow `AI09 canonical development capacity diagnostic` działa na osobnym
runnerze GitHuba. Kod odrzuca pełny przebieg na lokalnym komputerze. Generacja,
kwalifikacja, eksport snapshot 1.1 z deklarowanymi planami, import i curated są osobnymi procesami;
pozostała pamięć wcześniejszej fazy nie przechodzi do następnej. Wspólny limit
czasu obejmuje wszystkie fazy. Co 0,2 s supervisor mierzy własny RSS i całe
drzewo workera, logiczny i zaalokowany scratch, wolny dysk i dostępną pamięć.
Jest to próbkowanie, więc krótsze skoki całego drzewa mogą pozostać niewidoczne.
Każdy zakończony worker zapisuje też własny systemowy peak RSS; przekroczenie
limitu przez ten dodatkowy pomiar odrzuca fazę. Czas CPU jest
próbkowaną dolną granicą workera i nie obejmuje CPU supervisora.

Limity próby: 4 GiB RSS, 8 GiB scratch, 3600 s, 6 GiB rezerwy dysku i 1 GiB
dostępnej pamięci. Przed startem wymagany jest dodatkowo wolny zapas równy
całemu budżetowi scratch/RSS. Przekroczenie limitu, awaria pomiaru lub błąd
workera zatrzymuje jego własną grupę procesów i zachowuje koszt oraz porażkę.
Nie zmienia limitów v11/v12, dawnych receiptów ani budżetów eksperymentów.

Artefakt GitHuba zachowuje plan z commitami i skrótami kodu/locków, pomiary
wszystkich rozpoczętych faz oraz ich ograniczone logi i metadane. Nie ma retry
generacji wewnątrz próby. Ewentualne kolejne uruchomienie jest odrębną, widoczną
próbą diagnostyczną. Nie wykonuje fitów, ocen modeli, końcowej generacji,
kalibracji lub promocji. Nie kwalifikuje `ai-training` ani całego AI 09.
Kontrakt 1.2 wymaga osobnego producenta z planem anomalii; samo włączenie
znanych planów forecast nie zmienia prawdziwej wersji snapshotu 1.1.

[Kontrolny odbiór](evidence/09-15-development-capacity-preparation.json) obejmuje
14 testów supervisora, w tym rzeczywistych procesów oraz native generację, kwalifikację,
eksport, import i curated małego `ai-load` 45 × 2 × 1 × 1. Otrzymano 2124
operacyjne wiersze snapshot/curated, 0 odrzuconych i `forecast_source: passed`.
To sprawdzenie połączeń API i obsługi zasobów; pełny canonical pomiar oczekuje
na publikację i uruchomienie. Wynik należy odczytać z konkretnego run/commita,
bez uznawania kontrolnych testów supervisora za odbiór danych.
