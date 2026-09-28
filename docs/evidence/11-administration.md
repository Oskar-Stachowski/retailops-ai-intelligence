# Odbiór administracji indeksami — etap 11

**2026-09-28 · trwałe runy i HTTP odebrane w zakresie test/fake.**
Implementacja `ae31935740fac2ac643d0bbe29d05bd2addf2c56` na `ai/rag-corpus`,
baza `5bb74b1`. [Pomiar JSON](11-administration.json),
[instrukcja](../knowledge-administration.md), [aktualny status](../STATUS.md).

## Zakres odebrany

Osobny grant `knowledge:index` i rola admin chronią zgłoszenie indeksowania,
odczyt runa i metadane bieżącego indeksu. Idempotencja wiąże środowisko,
principal i hash requestu; konfiguracja musi wskazywać wcześniej zatwierdzony
snapshot. Kolejka ma limit 100 oczekujących/wykonywanych runów na środowisko.

Jawnie uruchamiany worker zapisuje kandydata oraz raport techniczny.
Wznowienie zachowuje input, attempt i czas startu. Blokada sesji i token
przejęcia wykluczają równoległe wykonanie oraz publikację anulowanego workera.
Sukces nie aktywuje indeksu. Profile i raporty są niemodyfikowalne;
SQL kontroluje piny, przejścia stanów i wszystkie wymagane wyniki bramek.

Nowy [kontrakt runa wiedzy](../../contracts/knowledge/v1/knowledge-index-run.v1.schema.json)
używa wspólnego envelope i stanów. Kontrakt ML `run/v1` oraz bundle zachowują
dotychczasowe warianty training/forecast i kody błędów; odrzucają run indeksowania.

## Kontrole

- **542 testy** przechodzą w czystym checkoutcie. Odbiór na `e5f426b` używał
  świeżego venv i izolowanego wolumenu PostgreSQL (82,97 s). Po aktualizacji
  do finalnego commitu pełne sprawdzenie przechodzi ponownie na zachowanej
  bazie (78,10 s), potwierdzając odczyt wcześniejszych artefaktów.
- Ruff/check/format, strict Mypy (83 pliki), linki dokumentacji, snapshots
  intelligence/access/knowledge, wheel/sdist, Compose config oraz Gitleaks
  git/dir przechodzą. Nie dodano zależności runtime.
- PostgreSQL 16/pgvector 0.8.6 potwierdza sześć równoczesnych zgłoszeń jako jeden
  run, niezależność principal/environment, konflikt zmienionego body i odmowę
  niezatwierdzonej konfiguracji. Limit kolejki jest egzekwowany przed budową.
- Rzeczywisty proces workera po SIGKILL wznawia ten sam run. Drugi żywy worker
  jest blokowany; anulowanie odbiera poprzedniemu możliwość publikacji.
  Powtórzenie zakończonego runa nie zmienia wyniku ani wskaźnika indeksu.
- Nieprzechodząca bramka daje `failed/gate_failed` bez outputu. SQL odrzuca
  modyfikację pinów, terminalnych runów, profili i raportów oraz sukces oparty
  wyłącznie na etykiecie `passed`, gdy wymagany wynik boolean jest fałszywy.
- Osobny proces HTTP na loopback i rzeczywista DB potwierdzają 401/403/422/409,
  `Location`, odczyt trwałego runa, spójne metadane current, `no-store` i bezpieczne
  błędy. SIGKILL/restart i down/up zachowują pełne runy, profile i output.
  Własny oczekujący fixture jest anulowany dopiero po obu kontrolach retencji.

Powtarzalny smoke używa osobnych document IDs dla każdego przebiegu, zachowując
wcześniejsze blokady dostępu i reuse cache treści. Wcześniejsze kontrole retrieval,
lifecycle, awarii DB, MLflow i izolacji nadal przechodzą. Migracje świeżej bazy
oraz istniejącej lokalnej bazy osiągają `0006_rag_job_gate`. Odczyt current
w bazie rzeczywistego korpusu zwraca `not_active`.
Stosy odbioru zatrzymano, zachowując wolumeny.

## Granice i dalsza praca

Profile odbioru są syntetyczne: `environment=test`, provider `fake`, polityka
`offline-index-mechanics-v1`. Raport ma `activation_allowed=false`; nie mierzy
jakości semantycznej. Rzeczywisty kandydat 20 dokumentów/302 fragmentów,
rejestr źródeł i golden labels pozostają niezmienioną propozycją. Nie utworzono
dla niego zgody ani profilu administracyjnego i nie aktywowano indeksu.

Aktualny postęp opisuje [status](../STATUS.md), a kontrola podobnych treści ma
[osobny odbiór](11-similarity.md). Pozostają odświeżenie i przegląd źródeł/statusów/dostępu/etykiet,
profil użytkowy z kwalifikacją golden oraz powiązanie release manifestu.
Real embeddings, Bedrock i agent mają osobny odbiór w etapie 12.
Commity zapisano lokalnie; nie wykonano push ani zdalnego Required CI dla tego zakresu.
