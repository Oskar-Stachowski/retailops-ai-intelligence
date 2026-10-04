# AI 09.5 — odbiór wspólnego rejestru development

Status całego AI 09: **in_progress / not_ready**. Przyrost dodaje
[wspólny rejestr](../development-trial-registry.md), rezerwację przed fitami,
budżet pomiędzy outputami i retrospektywną inwentaryzację artefaktów.
Nie uruchamia nowego benchmarku na danych projektu ani końcowej oceny.
Rzeczywiste treningi kontrolne należą wyłącznie do małych testów CPU.

Własny odrębny worktree AI 09 zachowuje sesje AI 07/08/10 i oryginalny checkout.
Odczyt sesji AI 08 potwierdził trwające prace temporal storage po commicie
`077add2c`; ten przyrost nie dodaje kolejnego czytnika pamięci.

## Wykonane kontrole i historia

81 testów zakresu przeszło: 23 kontrole rejestru i dotychczasowe 58 testów
pamięci, inferencji, porównania oraz challengera. Kontrole obejmują wyścig
dwóch procesów o ostatnie miejsce, SIGKILL po rezerwacji, niezmienność
terminalnego wyniku, awarię atomowej podmiany, uszkodzenie historii,
brak ledgeru, zmianę ścieżki, próbę zmiany kodu/protokołu oraz blokadę
przed odczytem rodziców po wyczerpaniu budżetu. Niepowodzenie przed fitem
zachowuje oryginalne pliki, a brak wyniku po SIGKILL nie zwalnia limitu.

Historia obejmuje 11 jawnie wskazanych katalogów 09.3/09.4: pięć zakończonych
porównań i sześć przerwanych prób pamięci. Dzienniki zawierają 44 rozpoczęcia
i 40 zakończeń fitów. Sukces fitu nie oznacza zakończenia całego pipeline:
dwie przerwane próby zdążyły zakończyć wszystkie cztery modele.
To obserwacja po wykonaniu, nie wcześniejsza rezerwacja ani kwalifikacja jakości.
Checksums obejmują istniejące pliki kosztu i częściowe artefakty.
Kontrola zainstalowanego wheel poza repo odtworzyła tę samą historię i trzy
schematy; wszystkie 268 plików kodu i 55 załadowanych modułów pochodzą z pakietu.
Nie importowała frameworka TensorFlow. Osobna kopia zakończonego wyniku
po zmianie `predictions.jsonl` została odrzucona przez publiczne `verify-history`
z kodem 2; oryginalne predykcje i ledger pozostały niezmienione.

## Błąd zdalnego CI wcześniejszego przyrostu

Required CI 146 dla `0b8b1ae` zakończyło się failure w teście TensorFlow
na Ubuntu x86_64. Persistence i secrets przeszły. Test porównywał z użyciem
ścisłej równości wartości z istniejącego loadera w procesie testowym i świeżego
procesu CPU z wyłączonym oneDNN. Przykład: 11.644882202148438 vs
11.644885063171387. Różnice mieściły się w dotychczasowym kontrakcie reload
`rtol=1e-6 / atol=1e-6`. Kontrola korzysta teraz z tych przypiętych tolerancji,
pełnych kluczy i zgodności przedziałów. Replay dwóch izolowanych uruchomień
nadal wymaga identycznych bajtów. Progów jakości ani limitów nie zmieniono.

[Wersjonowany receipt](09-05-development-trial-registry.json) wiąże dokładne
artefakty, testy, pakiet oraz pełną regresję. `make ci-local` przeszło w
1603,40 s: **1862 testy główne** w 1366,26 s i **3 rzeczywiste testy CPU
TensorFlow** w 54,37 s, bez skips. Ruff sprawdził 575 plików, Mypy 343;
przeszły wszystkie kontrakty, wykonywalne bramki, sdist/wheel, konfiguracja
Compose i oba skany sekretów. Compose sprawdzał konfigurację, bez startu usług.
Wheel z pełnego CI jest identyczny bajtowo z odłączonym pakietem odbioru.
Po zmianie wyłącznie receipt/dokumentacji ponownie sprawdzane są docs i sekrety.
Nowy commit nadal wymaga własnego Required CI na Linuxie; stary failure
pozostaje failure i nie jest przenoszony na nowy wynik.

Do zamknięcia AI 09 pozostają większy bounded reader/batch training, niezależne
calibration/evaluation, finalny audyt dostępu do portfolio testu,
scenariusze/seedy, niepewność oraz końcowe polityki/karty AI 07/08 i lifecycle.
Holdout portfolio pozostał zamknięty. Promocja pozostaje zabroniona.
