# Pełne przygotowanie i trening anomaly w kampanii

`fit_campaign_anomaly` rezerwuje próbę development przed odczytem Source.
Sprawdza zamrożony plan, seed modelu, pełną recepturę Source oraz rzeczywiście
zakończony eksport zapisany w dzienniku. Błąd zużywa próbę i zachowuje koszt;
ponowny start wymaga wcześniej zadeklarowanego budżetu.

Worker odczytuje cały publiczny snapshot i curated, weryfikuje czystego
producenta oraz locki i odtwarza wszystkie natywne deklaracje dni. Wersjonowany
plan discovery ustala rzeczywistą liczność z całego strumienia. Przekroczenie
limitu odrzuca przygotowanie; nie zwraca mniejszego zbioru. Poprzedni plan
z dokładnym `expected_days` zachowuje swoją semantykę.

Replay obejmuje wszystkie zdarzenia z rzeczywistą chwilą `ingested_at` faktu
Source. Jest to jawna rekonstrukcja publicznych danych; nie jest dowodem
zaobserwowania live brokera ani deklaracją postępu publishera. Pełna bramka dnia,
cechy oraz membership zachowują wszystkie serie, daty i wykluczenia. Domyślna
polityka wykorzystuje wszystkie 11 cech. Numeryczny fit i progi wykorzystują
całe kwalifikujące się role train/validation.

Model powstaje po poprawnym zakończeniu wszystkich kontekstów parenta,
replay, dni, bramki i cech. Bundle zawiera cztery pliki: `model.json`,
`feature-manifest.json`, `parent-completion.json` i `plan.json`. Rzeczywisty
manifest wiąże model z kompletnymi cechami i planami. Nie nadaje mu fikcyjnego
identyfikatora dawnego, ograniczonego wejścia anomaly.

Osobny proces ładuje zapisany model i ponownie przelicza każdy zapisany wiersz
walidacji obu rodzin. Dopiero zgodność wyników, pełna weryfikacja bundle,
fsync oraz prywatny trwały receipt pozwalają zakończyć próbę jako `completed`.
Receipt przechowuje pomiary obu procesów, koszt CPU z zakończonymi dziećmi,
konserwatywny RSS, czas cold load i odtworzenia całej walidacji. Obowiązuje
zamrożony wspólny czas, scratch i RSS do 12 GiB oraz rezerwy hosta 1 GiB RAM
i 6 GiB dysku. Mniejszy budżet może zostać zadeklarowany przed startem.

[Dowód 09.71](evidence/09-71-campaign-anomaly-fit.json) rozróżnia testy
kontrolne od rzeczywistej kampanii. 15 kontroli trwałego dziennika przeszło.
Wcześniejsze dwa pełne publiczne warianty planned przeszły rzeczywisty fit
i świeży reload; zwykły zachowany fixture ma zmodyfikowanego producenta
i został poprawnie odrzucony. Nie zmieniono jego pochodzenia ani wymogu
czystego producenta. Końcowe kontrole wszystkich 11 cech, późnej odmowy
parenta, discovery, regresje oraz zainstalowana paczka pozostają do wykonania.

To implementacja adaptera development, a nie odbiór jakości. Nowa produkcyjna
kampania Project nie została uruchomiona, nie otwarto świeżego final i AI 09
pozostaje `not_ready`. Nadal wymagane są pełny lokalny i chroniony CI,
publikacja na main, niezależna prawda offline, pozostałe zastosowania,
rzeczywista pełna kampania oraz końcowa kwalifikacja.
