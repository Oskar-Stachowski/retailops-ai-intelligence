# AI 09 — wspólny rejestr prób development

**2026-10-07:** dawne prywatne katalogi rejestru i wyników w `/private/tmp`
nie istnieją. [Odbiór 09.5](evidence/09-05-development-trial-registry.md)
zachowuje inwentaryzację 11 prób, 44 rozpoczętych i 40 zakończonych fitów.
Nie potwierdza odtworzenia utraconych bajtów ani późniejszego zużycia budżetu.
Nowa kampania wymaga jawnego rozliczenia tej historii; nie wolno udawać
kontynuacji poprzedniego limitu przez pusty rejestr pod nową ścieżką.

Przyrost 09.5 dodaje jeden prywatny rejestr dla wielu katalogów wyników.
Przypina jego bezwzględną ścieżkę, pełne protokoły, kod audytu i budżet nowych
uruchomień. Rezerwacja jest zapisywana przed odczytem rodziców i przed fitem.
Zmiana outputu, restart, błąd oraz nierozliczona próba nie odnawiają limitu.
Każda rezerwacja zajmuje konserwatywnie cztery miejsca treningowe:
RF mean, HGB mean, HGB median oraz TensorFlow, także gdy pipeline zatrzyma się
wcześniej. Trzy empiryczne baseline'y nie wykonują fitu.

Plan pozwala na maksymalnie dwie próby jednego dokładnego protokołu. Liczba
nowych uruchomień całego planu jest osobnym, jawnie podanym limitem.
Fity zachowują swoje dotychczasowe limity CPU/RAM/czasu; rejestr nie zastępuje
ich supervisorów ani pomiaru zasobów całego pipeline. Nie wybiera zwycięzcy.

## Trwałość i zakres

`ledger.json` wiąże SHA-256 planu oraz kolejnych rezerwacji i wyników.
Pod blokadą `flock` każda zmiana ponownie sprawdza całą historię i budżet.
Nowy plik jest zapisany 0600, flush/fsync, atomowo podmieniony i fsyncowany
w katalogu 0700. Runner zaczyna pracę dopiero po trwałym zapisie.
Niepełny zapis, inny kod/protokół, duplikat outputu lub uszkodzony łańcuch
blokują uruchomienie. Brak ledgeru w istniejącym katalogu nie inicjalizuje
nowego budżetu. Kopia rejestru pod inną ścieżką także nie otwiera nowych prób.

Nagłe przerwanie pozostawia rezerwację `unresolved`; nie ma automatycznego
ponowienia, zwrotu limitu ani udawanej zerowej wartości kosztu.
Zakończony wynik ma checksums wszystkich plików, status, liczbę rozpoczętych
i zakończonych fitów oraz wskazania oryginalnych receipts zasobów.
Brak pełnego kosztu procesu pozostaje nieznany. Ponowienie tego samego
terminalnego zapisu jest idempotentne, a inny wynik go nie nadpisuje.

Granica audytu to wskazany wspólny rejestr i zarejestrowany runner. Dotychczasowy
`tensorflow_challenger.cli compare-development` pozostaje samodzielną
diagnostyką i nie udaje, że został objęty nowym rejestrem. Rejestr nie wykrywa
automatycznie wszystkich poleceń uruchamianych na komputerze. Nowy plan
pod inną ścieżką jest osobną kampanią, z jawną historią; nie jest kontynuacją
poprzedniego limitu. Prywatny plik i hash nie chronią przed właścicielem,
który świadomie przepisze cały rejestr. Eksport oraz hash głowy trzeba
zachowywać razem z backupem poza katalogiem roboczym.

## Historia istniejących wyników

`freeze --historical-attempt` odczytuje stare artefakty bez ich zmiany.
Sprawdza protokół, lokalny dziennik, konfiguracje fitów, komplet checksums
sealed wyników oraz jawne oznaczenia przerwania. Oryginalne pliki kosztu,
niepowodzenia i częściowe wyniki pozostają widoczne. `verify-history`
ponownie porównuje całą zawartość ze stanem zapisanym w planie.

To **retrospektywna inwentaryzacja**, nie dowód, że stary budżet zamrożono
globalnie przed fitami, ani nowa kwalifikacja modeli. Kontrola plików
nie odtwarza modelu i nie otwiera etykiet. Protokoły starszego kodu zachowują
swoje hashe; przyrost 09.5 ma nowy comparison code binding z kontrolą
zamrożonego protokołu przed pierwszym fitem. Replay historycznych porównań
wymaga kodu przypiętego w ich manifestach.

## Polecenia

Użyj jednego trwałego, prywatnego katalogu rejestru dla całej kampanii.
Protokół jest obliczany na tych samych zweryfikowanych rodzicach co benchmark.
Ta operacja sprawdza istniejący development split, którego wcześniejsze
etykiety development holdout zostały już odczytane. Nie nazywa ich nietkniętymi.

```sh
uv run --project environments/tensorflow --locked \
  python -m retailops_ai.evaluation_campaign.trial_cli protocol \
  --features /path/to/features --split /path/to/split --curated /path/to/curated \
  --fold development-v1 > /private/tmp/development-protocol.json

uv run --project environments/tensorflow --locked \
  python -m retailops_ai.evaluation_campaign.trial_cli freeze \
  --registry /path/to/new-private-registry \
  --protocol /private/tmp/development-protocol.json --maximum-new-attempts 1 \
  --historical-attempt /path/to/previous-comparison

uv run --project environments/tensorflow --locked \
  python -m retailops_ai.evaluation_campaign.trial_cli run \
  --registry /path/to/new-private-registry --protocol-sha256 HASH \
  --features /path/to/features --split /path/to/split --curated /path/to/curated \
  --output /path/to/new-comparison

uv run --project environments/tensorflow --locked \
  python -m retailops_ai.evaluation_campaign.trial_cli inspect \
  --registry /path/to/new-private-registry

uv run --project environments/tensorflow --locked \
  python -m retailops_ai.evaluation_campaign.trial_cli verify-history \
  --registry /path/to/new-private-registry
```

SHA protokołu to canonical SHA-256 pełnego `DevelopmentProtocol`;
`inspect --full` pokazuje zamrożone protokoły i cały ledger. Wielokrotne
`--protocol` i `--historical-attempt` są jawne, bez automatycznego globowania
cudzych katalogów. Nowy fit wymaga zgodności całego protokołu z aktualnie
zweryfikowanymi rodzicami, polityką, kodem i środowiskiem.

[Odbiór 09.5](evidence/09-05-development-trial-registry.md) rozróżnia historię
i nowe kontrole awarii. Finalny rejestr dostępu do testu portfolio, niezależna
kalibracja/ocena, większa skala oraz końcowe polityki AI 07/08 pozostają otwarte.
Wszystkie kontrakty zachowują `not_ready` i brak zgody na final test/promocję.
