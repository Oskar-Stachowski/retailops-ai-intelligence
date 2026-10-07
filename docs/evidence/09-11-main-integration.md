# AI 09.11 — integracja po zamknięciu AI 07–08

AI 09 integruje gotowy consumer main `18e771f9` i zachowuje odbiory AI 07–08.
Rozwiązano sześć konfliktów konfiguracji, pakowania i statusu. Kod aplikacji
wcześniejszych etapów scalił się bez konfliktów. Obraz API i wheel zawierają
zarówno kontrakty AI 07–08, jak i evaluation oraz lock TensorFlow.

Required CI zachowuje cztery pełne shardy testów, wszystkie grupy odbioru,
skan sekretów, persistence i anomaly OCI. Osobny wymagany job uruchamia
`make bootstrap tensorflow-check`; usunięcie, pominięcie lub zastąpienie treningu
jest wykrywane przez nowe kontrole negatywne. Końcowy job wymaga sukcesu każdej
grupy. Wyjątki publicznych digestów są ograniczone do reguły `generic-api-key`,
dokładnych ścieżek i wartości. Ten sam hash w innym pliku oraz inny hash w tej
samej ścieżce nadal są wykrywane.

Po integracji zaliczono **420 testów w 509.43 s**. Po aktualizacji środowiska
TensorFlow zaliczono **114 testów w 21.73 s** oraz **3 rzeczywiste testy Keras
w 104.42 s**: trening, wspólny benchmark, supported MLflow artifact, powtarzalność
i świeży CPU reload z tolerancją `1e-6`. Lint, format, Mypy (601 plików), linki,
pełne kontrakty, pakiet i Compose config przeszły. Skan Git i directory oraz
kontrole negatywne wyjątków są zaliczone. [Receipt](09-11-main-integration.json)
wiąże logi, runtime i granice każdej próby. Pełny zdalny Required CI oraz
publikacja na main pozostają osobnym odbiorem dokładnego commita.

Pierwsze trzy testy TensorFlow nie przeszły. Świeża instalacja SciPy 1.15.3 na
macOS nie ładowała PROPACK z powodu niepoprawnego Mach-O (`__thread_bss`).
MLflow degradował inferowany podpis do `Any` i odrzucał zapis modelu, a worker
drzew nie startował. Zaktualizowano wyłącznie osobny lock i zapisywane wymagania
TensorFlow do **SciPy 1.17.1**, już używanego w głównym środowisku. NumPy,
TensorFlow, Keras i główny `uv.lock` nie zmieniły się. Pierwotne błędy i próby
diagnostyczne pozostają rozliczone; wcześniejsze artefakty nie uzyskują
automatycznie nowej kwalifikacji środowiska.

Dawne prywatne katalogi AI 09 w `/private/tmp` nie istnieją. Nowy worktree jest
w trwałym katalogu `/Users/oskarstachowski/.codex/worktrees/retailops-ai09-ready`.
Nie odtworzono brakujących bajtów dzienników i nie zresetowano budżetu.
Wersjonowane dowody zachowują 11 prób, 44 rozpoczęte i 40 zakończonych fitów
oraz ostatni stan czterech planów / zero nowych rezerwacji projektu.
Pełna ekspozycja poza tym audytem jest nieznana. Przed kolejnymi eksperymentami
wymagane jest jawne, konserwatywne odzyskanie lub przeniesienie historii.

Ten odbiór obejmuje integrację i kontrolowane dane testowe. Nie otwarto nowego
final testu projektu ani nie wykonano nowych projektowych fitów lub promocji.
Nie zmieniano worktree, danych ani procesów innych sesji.

AI 09 nadal jest **in_progress / not_ready**. Do zamknięcia pozostają rzeczywiste
powiązanie źródła z pięcioma rolami, fair training i kalibracja, zamrożona kampania
trzech seedów/scenariuszy, segmenty, niepewność, koszty, trzy raporty/karty oraz
decyzje lifecycle. Wymagany profil `ai-training` ma 730 dni, 200 produktów,
10 aktywnych par sprzedaży i 4 stock locations (do 1 460 000 daily rows).
Mały smoke i istniejące limity czytników nie kwalifikują tego profilu.
