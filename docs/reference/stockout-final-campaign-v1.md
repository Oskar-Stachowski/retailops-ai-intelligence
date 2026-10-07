# AI 08 — propozycja końcowej kampanii v1

To dokładny zakres do zatwierdzenia, przed pierwszą oceną wyników końcowych.
[Manifest](stockout-final-campaign-v1.json) przypina sześć rzeczywistych źródeł,
ZIP/checkpoint/resource SHA, wersje producenta i konsumenta oraz rozdziały czasu.
Łącznie jest 9296 kwalifikujących się punktów: matching 477/473/482 i późniejsze
2627/2609/2628, po jednym raporcie dla każdego seeda 42/137/2026. Światy nie są
łączone w jeden wynik i nie traktujemy powtórzonych SKU jako niezależnych kopii.

Jedyny oceniany model jest wybrany wcześniej na development: LR with_upstream
z conditional sigmoid C=10. Pełna [receptura](stockout-final-selected-recipe.json)
i [propozycja polityki](stockout-final-selected-policy.json) mają osobne przypięte
sumy. Nie ma nowego treningu, kalibracji, wyboru modelu ani progów na TEST.
Porównanie do wyniku bez kalibracji służy diagnostyce tego samego modelu.

Progi to 25% / 50% / 90%; globalna kolejka wybiera liczbę pozycji równą 20%
kwalifikujących się w danej chwili origin, zaokrągloną w górę, z jawnymi remisami. Kategorie
i lokalizacje nie dostają osobnych limitów. Koszty 1 za fałszywe wskazanie i 5
za pominięcie są jednostkami porównania, nie kwotami w złotych. Na development
limit wychwycił 97 z 218 zdarzeń (44%) przy zerze fałszywych wskazań.

Bramki pozostają te same: każdy wymagany segment ma co najmniej 20 punktów
i po 5 przypadków obu klas, AP powyżej częstości zdarzeń, Brier lepszy od stałej
TRAIN 550/1305 oraz błąd kalibracji ECE najwyżej 0,15. Raport obejmuje wszystkie
8 kategorii, 2 fizyczne lokalizacje i ograniczenia zapasu. Każdy brak wsparcia
lub niezaliczona kontrola pozostaje widoczny i blokuje odbiór jakości.

W każdym późniejszym świecie wymagane są normal/promotion/demand_shock/
inventory_constraint. Promocja wymaga planu znanego w origin i przecięcia
aktywnego asortymentu oraz trasy fizycznego zapasu z oknem kolejnych 7 dni.
Szok dotyczy z góry ustalonych SKU (SHA256 mod3=0) i okna 28 lipca–3 sierpnia
w profilu stress, obejmując origin, którego horyzont je przecina. Ograniczenie
zapasu to dodatnia liczba constrained days w historii znanej w origin.
Normal nie ma żadnej z tych ekspozycji. Te grupy mogą nakładać się; raport
pokazuje przecięcia i grupy kontrolne, bez twierdzeń o efekcie przyczynowym.
Każda wymagana grupa kontrolna musi zawierać co najmniej 20 punktów; jej metryki
bez obu klas pozostają not_evaluable i nie uzasadniają przyczynowych wniosków.

Ocena pobierze istniejące archiwa na odrębne GitHub runners. Nic nie jest
regenerowane lokalnie. Rezerwa lokalnego dysku pozostaje 50 GiB; na zdalnym
runnerze pozostaje 6 GiB. Dostęp do etykiet ma dziennik i wymaga osobnego pliku
zgody na dokładny campaign ID. Zgoda na ocenę i politykę nie jest zgodą na
promocję modelu. Wynik kampanii nie zamyka samodzielnie całego AI 08.
