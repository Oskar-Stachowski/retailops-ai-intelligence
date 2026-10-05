# AI 08 — prawdziwa kwalifikacja i pełna karta modelu

Pełny replay publicznych features/upstream2.1 przeszedł na izolowanym runnerze
[37278322783](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37278322783),
commit `c8126d5033b856b210cc391536bc3580fb0fbac5`, job `111660275708`.
Krok qualifier i porównanie dwóch plików końcowej jakości z pierwotnym runem
zakończyły się sukcesem. Cały ten run później zatrzymał fałszywy alarm Gitleaks
w publicznej sumie planu AI09 z fetchowanej historii, więc nie deklarujemy
sukcesu całego runu. Wąski wyjątek dotyczy wyłącznie konkretnego pliku i SHA256;
pełna historia z poprawioną konfiguracją przechodzi.

[Kwalifikacja](08-26-qualification.json) ma purpose `qualified_stockout`, jakość
`passed_independent_final_campaign` i 40 punktów smoke. ID:
`stockout-qualification-serving-sha256-0a31aa522287a8f52aa5424dcb289c446b209f4a35dec3fb53735d5825df9bf8`.
[Karta modelu](08-26-model-card.json) zachowuje porównanie LR/HGB, komplet kalibratora,
progi/capacity, współczynniki, sześć osobnych wyników, trzy ostrzeżenia, lineage
i ograniczenia danych syntetycznych. Kwalifikacja sama nie jest promocją.

[Pobranie](08-26-qualification-download-verification.json) sprawdziło cały ZIP
artefaktu `11331641405` (471064B) względem SHA256
`935a89bf3824fda5a5cce1b07dc84ad71fe89928dbcde79373304ccb66ddd6ff`.
Cold verifier ponownie sprawdził tożsamość, wszystkie wymagane pliki, model/policy,
receipts, ważność, obecny adapter oraz identyczne sumy jakości i wykonania.
Powtórka integracji korzysta z tej samej kwalifikacji, bez restauracji źródeł,
otwierania etykiet, oceny TEST, treningu lub kalibracji. Ważność pakietu do
2026-10-06T07:38:07.956794Z jest osobnym ograniczeniem serving, nie trwałością
merytorycznego odbioru etapu.

## Porównanie przenośnego smoke

Run integracji `37279886538` zaliczył ponowne sprawdzenie kwalifikacji, Gitleaks,
204 focused tests i budowę rzeczywistych obrazów. Review ujawnił wymaganie
bitowej równości prawdopodobieństw między procesorami. Dla zachowanej paczki
replay na macOS różnił się maksymalnie o `2.220446049250313e-16`.

Verifier kapsuły stosuje teraz wyłącznie do pola probability absolutny próg
`1e-12`, będący już używaną dolną tolerancją portable/sklearn parity. Wszystkie
pozostałe pola, kolejność, band, lineage i identyfikatory pozostają dokładne;
sumy kontrolne przechowywanych bajtów również pozostają dokładne. Test z jedną
jednostką zaokrąglenia przechodzi, zmiana o `1e-6` oraz zmiana model_version
są odrzucane. Native/wheel kampanii jakości nadal ma identyczne bajty.
Nie zmieniono żadnej bramki jakości, modelu, polityki ani frozen evaluator digest.

Końcowy rzeczywisty review/MLflow/lifecycle/batch/API oraz wymagane CI i scalenie
pozostają osobnym odbiorem. **Ten dokument sam nie deklaruje AI08 ready.**
