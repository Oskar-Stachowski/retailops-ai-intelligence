# Odbiór lokalny AI 05.4a — kolejka i worker

Data: **2026-09-30**. [Raport rzeczywistego testu](05-04-queue.json)
powstał przez `.venv/bin/python scripts/check_forecast_queue.py` na branchu
`ai/05-mlflow-serving`. Test korzystał z pakietu w zbudowanym obrazie,
PostgreSQL, MLflow, rzeczywistego HTTP i osobnych procesów wykonania.
Projekt `retailops_ai_queue_116fca0409` nie publikował portów hosta i został
usunięty wraz ze swoimi wolumenami. Stosy innych sesji nie były zatrzymywane.

## Zmierzony zakres

- 9 runów, 12 niezmiennych zamkniętych prób i 2 kompletne receipts po 14
  predykcji testowych; nie było aktywnego lease po zakończeniu odbioru.
- HTTP zwrócił `202` z `Location`, zachował run przy powtórzeniu klucza,
  odrzucił zmienione żądanie `409`, viewer batch `403` oraz obcy scope `404`.
- Zmiana `champion` z wersji 1 na 2 po przyjęciu runu nie zmieniła modelu
  ani danych. Osobny supervisor/child użył przypiętej wersji 1 i zapisał
  wszystkie 14 poprawnych wierszy fixture wraz ze statusem `succeeded`.
- Niepełny grain został odrzucony bez publikacji outputu. Dwa połączenia
  ścigały się o zadanie; tylko jedno otrzymało lease. Heartbeat przedłużył
  lease, a anulowanie uniemożliwiło zapis wyniku przez wcześniejszego workera.
- Proces testowy przejął zadanie i obliczył pierwszy wiersz w pamięci.
  Rzeczywisty SIGKILL przerwał go przed publikacją. Kolejna próba przejęła
  ten sam run z nowym tokenem i numerem 2. Historia zachowała pierwsze
  `failed` i późniejsze `succeeded`, identyczne piny danych/modelu oraz jeden
  kompletny output. Stary token nie mógł wysłać heartbeat ani wyniku.
- Retry zatrzymał się na trzeciej próbie i zachował wszystkie błędy.
  Sprawdzono limity globalne i na konto, oddzielne namespace klucza,
  deadline próby oraz anulowanie po przekroczeniu czasu oczekiwania.
- SIGKILL i restart PostgreSQL/MLflow zachowały pełny obraz runów, profili,
  historii i outputów, potwierdzony identycznym SHA-256 przed/po restarcie.
  Baza odrzuciła update niezmiennych profili, historii i receipts.

## Kontrole kodu

Testy obejmują ścisły kontrakt i limity, odmowę identity/role/path injection,
cały scope, bezpieczne błędy, duplikat nagłówka, ponowną kontrolę checksum
dokładnie załadowanych bajtów modelu po walidacji Registry, zamknięty intelligence/run
v1 oraz zabicie własnego procesu obliczeń po utracie lease. Nowa bramka
`forecast-queue-smoke` należy do Required CI i ma test uniemożliwiający jej
usunięcie z obowiązkowej ścieżki. Końcowa regresja: **983 passed** (620,09 s), z pominięciem czterech
niezmienionych modułów ciężkich eksperymentów forecasting: models, backtest,
quality i run. Dodatkowe **27 testów forecast_jobs** przechodzi po dopracowaniu
checksum; obejmują nowy przypadek podmiany artefaktu. Nie sumujemy tych
wyników, ponieważ zakresy częściowo się pokrywają.

Ruff i format przechodzą dla 226 plików, mypy dla 188 modułów kodu/skryptów.
Pakiet wheel/sdist buduje się; wszystkie snapshoty kontraktów (w tym
niezmieniony intelligence v1), linki dokumentacji i guard Required CI
przechodzą. Gitleaks sprawdził pliki i dotychczasowe 101 commitów bez wycieków.
Pełne `make ci-local` i zdalny Required CI pozostają bramką przed PR;
bieżący commit nie obejmuje nowego pomiaru jakości AI 04.

## Granice i dalsza praca

Odbiór używa `retailops-demand-forecast-mechanics`, sztucznych wejść i
`lifecycle_mechanics_only`, z `forecast_quality_approved=false`.
Nie stanowi zatwierdzenia jakości AI 04, kompletnego AI 05 ani publikacji
rzeczywistych prognoz. CLI obliczeń jest włączone tylko w `APP_ENV=test`;
środowisko local odmawia nieprzygotowanych wejść. Lista prognoz nie jest
jeszcze zaimplementowana, więc receipts testowe nie trafiają do jej
wyników domyślnych. Rzeczywisty profil cech/adapter modelu i atomowe wyniki
pozostają w AI 05.5–05.6, read API w 05.7 i publikacja zdarzeń w AI 10.
Zmiany są lokalne; zdalny Required CI nie był uruchamiany w tej sesji.
