# AI 08.15 — niezależna ocena kalibracji i karta rzeczywistych partycji

**Odebrano artefakt i odtwarzalność; jakość modelu ma `not_ready`.**
[Receptura](../reference/stockout-independent-qualification.md),
[polityka](../reference/stockout-independent-development-policy.json),
[profil 1.2](../reference/stockout-resource-pilot-1.2.json) i
[receipt](08-15-stockout-independent-development.json) rozdzielają te wyniki.
Wcześniejsze pakiety, ich polityki i tożsamości pozostają zachowane.

## Rzeczywiste dane i zasoby

Cały nowy pipeline 19 produktów × 2 fizyczne lokalizacje × 102 dni
ma 3876 origin. Producent jest przypięty do `08639e9`, wcześniejsze pakiety
konsumenta do `4faaf4b`. Nowe źródła przechodzą qualification, oba
eksporty/importy, curated, features 2.2, labels 2.0, upstream 2.1,
temporal 2.1 i sześć fitów. Wszystkie źródła oraz rodzice są zachowane
w osobnym własnym `retained_root`, wskazanym przez receipt.

| Pomiar | Pilot 14 produktów | Pilot 19 produktów |
|---|---:|---:|
| Wszystkie fizyczne origin | 2856 | 3876 |
| Train / tune / calibration | 598 / 227 / 218 | 839 / 321 / 313 |
| Development razem | 1043 | 1473 |
| Cały pipeline | 719,81 s | 1243,65 s |
| Peak własnego drzewa RSS | 622,23 MiB | 492,09 MiB |
| Peak allocated scratch | 259,23 MiB | 325,79 MiB |
| Kategorie z obiema klasami na tune | 7/8 | 8/8 |

Preflight wykorzystuje faktyczny ukończony pilot, nadal z 20% zapasem,
limitem 1 GiB RSS i rezerwą 50 GiB. Actual minimum free wynosi 52,02 GiB.
Niższy peak RSS większej próby jest rzeczywistym pomiarem, nie założeniem
o skalowaniu. To pośredni profil, nie pełne `ai-dev` lub `ai-training`.

## Ocena poza dopasowaniem kalibratora

Oryginalne train jest dzielone według ścisłego czasu dostępności etykiet:
370 punktów dopasowuje model przed 20 maja, późniejsze 232 punkty
dopasowują sigmoid przed 5 czerwca. 237 punktów przecinających granice
jest purged. Oryginalne tune 321 wybiera rodzinę na raw AP/Brier.
Późniejsze 313 punktów original calibration służy wyłącznie niezależnej
ocenie zamrożonego modelu i sigmoidu. Role mają oddzielne skróty kluczy/celów
i nie nakładają się. Final test ma wyłącznie membership, bez outcome vector.

Wybrany wariant: `logistic_regression:without_upstream`.
Na niezależnym development ma AP 0,991449 przy prevalence 0,488818;
raw Brier 0,035440, po sigmoidzie 0,050294. Sigmoid pogarsza Brier w tej
próbie. Całościowy ECE wynosi 0,049873, ale dwie kategorie przekraczają
proponowane 0,15: ECE 0,231373 i 0,167579. Segment historycznie
inventory-constrained ma 149 punktów, 146 pozytywnych i tylko 3 negatywne
wobec wymaganego minimum 5 każdej klasy. Otrzymuje `not_evaluable`.
Wszystkie kategorie i obie lokalizacje są obecne; liczebność oraz jakość
pozostają osobnymi bramkami. Nie zmieniano wymagań po tym wyniku.

## Native, wheel i karta

Publiczny builder ponownie odtwarza wszystkich rodziców, sprawdza ich seals
przed i po obliczeniach oraz tworzy nową kartę 2.0 bez fikcyjnych v1 IDs.
Karta pokazuje rzeczywiste rodzice, trzy tabele współczynników LR,
permutation importance na tune, factual context i niezależne wyniki.
Factual context nie jest dowodem przyczynowości.

Native trwa 277,59 s, peak RSS 267,14 MiB. Wheel trwa 217,88 s,
peak RSS 309,58 MiB. Oba mają limit 1 GiB RSS, 512 MiB własnego scratch,
600 s i rezerwę 50 GiB. Minimum free obu przebiegów przekracza 51,82 GiB.
87 modułów konsumenta pochodzi wyłącznie z instalacji; korzenie producenta
`data`, `ml`, `retailops` nie są importowalne.

Capsule ma 1 325 720 B i identyczne bajty native/wheel, SHA
`5c909b24c87372a817ebdd2d6ef8dcabf2dfa5b73f8a7433c6a0ab56f4bf4918`.
Qualification ID kończy się `2ad4bc02b539ec8f75fae6a79944f4b51599c849a7d740cd484177e17b74af7c`,
card ID `c573b49af2d8950ed49ccafc888faa71c59542b430e3e958e19a227aeaf0ce28`.
Immutable zapis `published` i powtórny `reused` są sprawdzone w obu trybach.
Exit 0 odbioru artefaktu nie oznacza zaliczenia jakości ani pozwolenia na
publikację modelu do serving.

100 testów ma 0 failures/0 warnings: 27 kwalifikacji, 36 pomiaru zasobów,
37 istniejącego treningu/wyjaśnień. 34 przypadki są nowe wobec rodzica.
Testy celowo zmieniają validation targets/features: preprocessing, model,
sigmoid i wybór rodziny pozostają identyczne. Odrzucają niepoprawne role,
granice dostępności, duplikaty, brakujące segmenty, za mało klas, błędne
metryki, zmianę rodzica w czasie fitu oraz niekwalifikujący się stock.
Ruff, format i mypy przeszły dla zamrożonego kodu; pełne CI nowego commita
pozostaje wymagane. Wczesne błędy strict JSON deserializacji karty są
naprawione; odebrano późniejszy przebieg 63 focused testów bez ostrzeżeń.

Otwarte pozostają poprawa jakości, robustness 3 seedów / 4 scenariuszy,
zatwierdzone progi/capacity, finalna kampania, lifecycle i trwały batch/API.
AI 05/v12 jest zachowane. Cały AI 08 pozostaje **not ready**.
