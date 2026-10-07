# Pomiar pełnego development AI 09

[Plan 1.1](reference/ai09-development-capacity-v1.1.json) określa prawdziwe `ai-dev`:
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

Limity nowej próby: 8 GiB RSS, 8 GiB scratch, 3600 s, 6 GiB rezerwy dysku i 1 GiB
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
To sprawdzenie połączeń API i obsługi zasobów.

[Pierwszy pełny pomiar](evidence/09-16-development-capacity-first-run.json)
na `cef4f08`, run `37611605538`, zakończył się `tree_rss_limit` po 127,875 s.
Generacja osiągnęła próbkowane 4295168000 B, ponad 4 GiB; nie ukończyła źródła,
więc kwalifikacja, eksport, import i curated nie rozpoczęły się. Supervisor
zatrzymał wyłącznie własnego workera (`exit_code: -9`). Plan i koszt są zachowane
w artefakcie GitHuba, bez automatycznego retry. To zmierzona dolna granica
potrzeb pełnego producenta; całkowity peak zakończonego profilu pozostaje nieznany.
Kolejny większy budżet wymaga osobnej prospektywnej receptury i własnego pomiaru,
albo ograniczenia pamięci producenta. Rozmiar canonical i stare limity pozostają
przypięte. Ten wynik nie kwalifikuje żadnej kampanii lub `ai-training`.

Osobna receptura 1.1 przypina tę porażkę i zachowuje [plan 1.0](reference/ai09-development-capacity.json)
bajt w bajt. Przed pierwszym startem runner miał 15532302336 B dostępnej pamięci;
to pozwala zaplanować próbę 8 GiB z dodatkową rezerwą 1 GiB. Wymagany preflight
i bieżące limity nadal działają. To nowy limit diagnostyczny, którego nie
uznajemy za zmierzony koszt ukończonego profilu. Kod producenta, pełny rozmiar
danych, historia, parent limits i zakazy fitów/final generation pozostają
przypięte. Pierwsza porażka pozostaje odrębnym
runem i nie jest nadpisywana.
Receptura 1.1 ma 17 zaliczonych testów supervisora i kontroli scope; obejmują
odrzucenie zmiany parent limits, rezerwy pamięci oraz skrótu wcześniejszego planu.

[Drugi pełny pomiar](evidence/09-18-development-capacity-second-run.json),
run `37613368332` na `d88d4a2`, zakończył się `wall_limit` po 3600.552 s.
Próbkowany peak własnego drzewa wyniósł 7679963136 B, poniżej limitu 8 GiB;
próbkowane CPU workera miało dolną granicę 3599.54 s. Generacja nie ukończyła
źródła; wszystkie późniejsze fazy pozostają nieuruchomione. Minimalna dostępna
pamięć wyniosła 8043921408 B. Artefakt i plan tej porażki są zachowane osobno.
Nie oznacza to, że ukończony profil mieści się w 8 GiB lub ma znany koszt.

[Source PR #101](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/101)
proponuje ograniczenie kopii wejść commerce i jednokrotne stosowanie ruchów
przy dziennych snapshotach, z fallbackiem dla spóźnionych faktów.
456 native testów przechodzi. Pięć kontrolnych native par zachowuje kompletne
wyniki; alokacje Python podczas kopii maleją o 14.81%, CPU dziennych snapshotów
o 89.62%. To oddzielne pomiary komponentów. Przed nowym pełnym pomiarem potrzebne
są publikacja producenta, jego CI oraz osobny plan zachowujący obie porażki.

Odbiór source PR #101 na `5bec26f9` zakończył się pełnym zielonym Required CI
[37623701164](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37623701164).
Chroniony merge opublikował `cbcac6eb` na source `main`;
[odbiór dokładnego main](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37629010997)
pozostaje w toku. Szybka receptura 1.2 jest przygotowana osobno i zachowuje
obie wcześniejsze porażki. Jej publikacja oraz pełny pomiar nadal są wymagane.

Core usprawnień CI z PR #34, main `89b64d2`, jest zintegrowany w tej gałęzi
bez zmiany `src` lub locków i bez przepięcia producenta AI07.
Przed chronionym merge tej publikacji wymagane są pełne CI dokładnego nowego
head oraz [main po PR #34](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37628897194).
