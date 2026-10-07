# AI 09 — wspólne prognozy development

`evaluation_campaign.campaign_score.score_campaign_forecast` wykonuje zamrożony
plan [v16](../contracts/evaluation/v16/campaign_forecast_score_plan.schema.json)
dla jednej roli `tune` albo `calibration`. Wymaga zakończonego audytowanego
eksportu oraz trzech zakończonych fitów RF/HGB/TensorFlow z tego samego journal,
źródła, danych i środowiska. Ich operacje są prerekwizytami scoringu. Plan oraz
limity muszą być przypięte w protokole przed odczytem.

Runner rezerwuje próbę przed odczytem manifestu, etykiet lub modeli. Osobne
procesy przygotowują indeks i wykonują świeżą predykcję na docelowym CPU.
Supervisor obejmuje wspólny czas faz, RSS własnego drzewa, scratch i rezerwy;
porażka zachowuje próbę oraz dostępny koszt. Nie ma automatycznego retry,
próbkowania ani zmiany limitów istniejących evaluatorów.

Indeks SQLite obejmuje wszystkie klucze wybranej roli. Sprawdza pełny hash,
liczbę wierszy, kolejność, cechy, historię i eligibility. Pozostałe pliki
etykiet nie są otwierane. Weryfikacja covariates obejmuje cały parent features,
bez użycia jego innych etykiet. Przetwarzanie okien ma ograniczony batch;
nie zapisujemy pełnej macierzy wszystkich prognoz w RAM.

Każdy wynik zawiera te same sześć pozycji: history7, history28, weekday28,
RF mean, HGB mean/median i TensorFlow mean/median. Wszystkie wiersze pozostają
w pliku, także censored i pozostałe wykluczenia, z pustymi predykcjami.
TF zachowuje częściowe okna i pozycję każdego z 14 horyzontów. Wektory korzystają
z funkcji wspólnych z treningiem; encoding i target scale pochodzą wyłącznie
z zakończonego fitu. Sprawdzamy również zgodność kluczy i etykiet treningowych
między rodzinami. Predykcje TF wracają do jednostek sprzedaży. RF nie dostaje
udawanej mediany; kandydaci nie mają jeszcze kalibrowanych przedziałów.

`predictions.jsonl` ma kanoniczną kolejność pełnych kluczy i hash każdego
wejściowego example. Diagnostyka mean i median jest osobna, globalnie i dla
każdego horyzontu. Stabilne sumy zachowują błędy, a WAPE i normalized bias
pozostają niezdefiniowane przy zerowym mianowniku. Brak poprawnych obserwacji
lub wymaganej funkcji prognozy nie daje idealnej metryki. Raport nie stanowi
kwalifikacji jakości ani segmentów końcowej kampanii.

Plan, rodzice, prognozy i metryki mają pełną kontrolę hashy i liczebności.
Trwały prywatny receipt pojawia się przed journal completion. Publiczny
`verify_campaign_forecast_scores` sprawdza go bez ponownego otwierania outcomes,
treningu albo przyznawania nowej próby. MLflow zapisuje plan, powiązania, metryki
oraz rzeczywisty koszt workera. Pełny payload predykcji pozostaje w audytowanym
katalogu, z przypiętym digestem w MLflow, bez drugiej kopii wielogigabajtowych
danych w tracking store.

Kontrole komponentów korzystają z jawnych typed fixtures i fake modeli lub
workerów, gdzie badają ordering i błędy. Osobny wymagany test CPU rzeczywiście
trenuje wszystkie trzy rodziny, ładuje ich pełne zapisane bundle i przewiduje
całą kontrolną rolę w świeżym procesie. Przygotowanie wejść tego testu korzysta
z kontrolowanego fixture, więc także jego sukces nie kwalifikuje canonical
source preparation ani projektowej kampanii.

Head `e4d6371` ma pełny [Required CI 37664383535](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37664383535):
17/17 success, łącznie z required-result. Rzeczywisty job CPU zaliczył
7 testów w 144.78 s, w tym nową wspólną predykcję sześciu modeli po zapisaniu
i świeżym odtworzeniu RF/HGB/TF. PR #39 integruje dodatkowo trening z PR #37
oraz poprawkę snapshotu z PR #40 i jest skierowany na main. Nowy dokładny head
tej integracji musi uzyskać własny pełny CI przed protected merge.

Ten przyrost nie otwiera `development_evaluation` ani final testu. Nie dopasowuje
kalibratora i nie wybiera zwycięzcy. Pozostają: zamrożona uczciwa selekcja na tune,
kalibracja na własnej roli, niezależna ocena po wyborze, pozostałe dwa zastosowania,
pełne profile i trzy końcowe seedy, robustness/niepewność/koszty, decyzje lifecycle,
trzy karty i raporty oraz protected merge i pełne CI końcowego main.

[Evidence przygotowania](evidence/09-25-campaign-scoring-preparation.json)
rozdziela zaliczone komponenty i odbiór CPU od niewykonanych wyników projektu.
