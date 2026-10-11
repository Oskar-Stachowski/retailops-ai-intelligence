# Wykonanie zamrożonego development forecast

`scripts/run_ai09_development.py` wykonuje istniejące dzienniki 25, 50 albo
100 produktów. Wywołuje rzeczywiste kontrolery eksportu, fitu, scoringu
i selekcji. Dla 25 wykonuje trzy eksporty, sześć prób, dwa porównania Tune
i wybór konfiguracji. Dla 50/100 wykonuje trzy eksporty, trzy wcześniejsze
zwycięskie konfiguracje, wspólny scoring i selekcję.

Dziennik musi już zawierać protokół `MultiscaleSelectionProtocol`,
`PreparedForecastAdvancementProtocol` albo `PreparedCanonicalForecastProtocol`.
Na gałęzi integracji dopuszczono także pełny, zamrożony przed pierwszym
eksportem `PreparedCanonicalQualificationProtocol` z 16 operacjami.
Osiem kontroli pełnej ścieżki i odmowy przeszło ze źródeł i z paczki;
odrębne 27 testów zachowuje regresję wcześniejszych kontrolerów.
[09.106](evidence/09-106-canonical-qualification.json) podaje pełny zakres.
Budżety, rodzice, środowisko, polityki i wcześniejsze koszty pochodzą
z [przygotowania danych](ai09-development-preparation.md) oraz
[zweryfikowanej selekcji](ai09-screening-selection.md).

Prywatny plik `DevelopmentExecutionPaths` podaje skrót protokołu, trzy pary
ścieżek snapshot/curated w kolejności ordinary/demand/physical, interpreter
workerów oraz katalog wyników. Wszystkie ścieżki są bezwzględne. Katalog
wyników musi istnieć z uprawnieniami `0700`; nie może znajdować się wewnątrz
dziennika ani oryginalnych rodziców. Interpreter dla wszystkich rodzin musi
spełniać wspólne, zapisane wcześniej przypięcie środowiska.

```sh
python scripts/run_ai09_development.py \
  --journal /private/campaign/25/execution \
  --paths /private/campaign/25/execution-paths.json \
  --maximum-new-operations 1
```

Limit jednej nowej operacji pozwala zakończyć proces na granicy operacji.
Domyślna wartość 12 wykonuje do 12 nowych operacji. Pełny dziennik kwalifikacji
obejmuje 16 operacji i wymaga kolejnego wywołania po takim zatrzymaniu.
Dla tego protokołu można jawnie podać limit 16 i wykonać cały dziennik
w jednym wywołaniu; wcześniejsze protokoły zachowują limit 12. Ścieżki są trwale
zapisywane przed pierwszym eksportem; ponowne wywołanie musi podać dokładnie
tę samą konfigurację. Log wypisuje rozpoczęcie, sprawdzanie i zakończenie
operacji. Wynik polecenia rozróżnia liczbę nowych operacji od całego
ukończonego prefiksu.

Wznowienie sprawdza oryginalne receipts, manifesty eksportu oraz artefakty
modeli i scoringu. Przekazuje wyniki następnemu kontrolerowi bez zmiany
wcześniejszych kosztów. Błąd albo rezerwacja bez zakończenia blokuje dalszy
start; wykonawca nie ponawia takiej próby i nie tworzy nowego dziennika.
Osobna nieblokująca blokada chroni przed drugim wykonawcą, a kontrolery
nadal rezerwują każdą operację w oryginalnym dzienniku.

Po selekcji wykonawca ponownie odbiera oryginalny wynik całej skali
i zapisuje niezmienny `forecast-execution-result.json`. Plik jest wejściem
do istniejącego kompilatora kolejnej skali. Samo ukończenie dziennika
zachowuje `quality_qualified=false`, `final_test_access_authorized=false`
i `stage_ready=false`.

Nowy dziennik kwalifikacji kontynuuje po Tune przez scoring Calibration,
kalibrator i kontekst oraz ocenę trzech wariantów. Nieprawidłowy wynik Tune
lub niewystarczająca kalibracja zatrzymuje dalsze operacje z `not_ready`.
Wynik `qualification-execution-result.json` zachowuje pełny protokół,
oryginalne skróty receipts i koszty. Ocenę development raportuje osobno;
`stage_ready` i dostęp final pozostają wyłączone.

**11 kontroli przeszło bez pominięć**, w tym wszystkie 12 operacji oraz
ponowny odbiór ukończonego dziennika bez startu workera. Pełny Mypy sprawdził
777 plików. **Te same 11 kontroli z paczki** również przeszło bez pominięć.
647 modułów i 23 wcześniejsze schematy mają identyczne bajty w źródłach,
paczce oraz instalacji.
To testy wykonawcy; rzeczywista kampania nadal wymaga przyjętego pina Source,
rzeczywistych zamrożonych polityk, przygotowania rodziców oraz danych i modeli
Project. Automatyczne przygotowanie wszystkich skal, pozostałe zastosowania,
kalibracja, final, lifecycle i odbiór PR/main pozostają otwarte.
