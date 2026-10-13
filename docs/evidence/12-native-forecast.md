# AI12 — adapter natywnej prognozy v12

Stan: **in_progress**, 2026-10-07, branch `ai/12-resume`,
[draft PR32](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/32).
[Receipt maszynowy](12-native-forecast.json) wiąże bieżący kod, konfigurację,
release, testy i pakiet. Jest to odbiór implementacji na jawnych fixtures;
kwalifikacja produkcyjnego runtime prognozy i modelu chat pozostaje otwarta.

## Zmiana

[Adapter](../agent-forecast-v12.md) przekazuje dokładny, ograniczony zakres
uwierzytelnionego operatora do `PostgresV12ForecastReader`. Natywny kontrakt
zachowuje całą stronę publikacji i obie predykcje candidate/baseline. Nie
odtwarza legacy ModelRecord. Weryfikuje namespace, środowisko, pełne pokrycie
żądanego grainu i dni, limit, origin, horyzont, unikalność i ważność approval.

Executor rozdziela świeżość dziennego origin do 24 h od wieku sprawdzonej strony
do 300 s. Ponownie sprawdza te daty przy użyciu. Graf przypisuje jawny mean
obserwowanej sprzedaży do właściwego źródła i release; brak mean lub danych
nie tworzy zera. Nie konwertuje natywnej prognozy do historycznej sugestii
uzupełnienia zapasu. Dotychczasowa ważność sugestii 300 s pozostaje bez zmian.

Planner dopasowuje limit do całej siatki prognozy, w istniejącym maksimum 20
wierszy; zbyt duży zakres jest odrzucany przed admission. Tydzień jednego
produktu/sklepu wymaga 7 wierszy i przechodzi cały HTTP → planner → graf → zapis.

## Walidacja

- 630/630 wybranych testów: agent, Assistant HTTP/store, uprawnienia, Bedrock
  doubles, dokumentacja, CI guards i lock tamper checks. W tym 41 natywnych
  przypadków pełnego wyniku, zakresu/tożsamości, namespace, budżetu, partial,
  unikalności, świeżości, expiry, błędów readera, grafu i HTTP.
- Pełne `make ci-checks`: Ruff, mypy 652 pliki, docs/CI guard, runtime,
  wszystkie kontrakty, fake golden 50/50 i krytyczne 36/36, build, Compose config.
- 534 pliki Python aplikacji mają te same bajty w wheel i checkout; oba
  lockfile'y pakietu odpowiadają ich plikom źródłowym. Gitleaks drzewa passed.
- Nie wykonano AWS, lokalnego treningu modeli ani pełnych eksportów Source.
  Nie zmieniono worktree, procesów, Compose ani baz sąsiednich sesji.

Nowe kandydaty `.native-v12.v1.json` mają osobne bindingi. Historyczne
manifesty, golden/oracles, modele i receipts nie są nadpisane. Bieżący release
to `agent-evaluation-release-sha256-829d4f0268f1f7b9ebd4a9b37dd338f12681ed8e493c6ffd3b2841a492363617`.
Etykiety pozostają proponowane, dopuszczone wyłącznie do jawnego testu offline.

## Zdalny odbiór i dalsze zależności

[Required CI poprzedniego checkpointu](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37615009993)
wiąże `3dd53c2`, sprzed tego adaptera. Checks, wszystkie cztery shardy, secrets,
cztery acceptance, persistence PostgreSQL/MLflow i rzeczywisty TensorFlow
artifact/reload są success; anomaly OCI nadal trwa. Ten wynik nie kwalifikuje
nowego code hash. Nowy head wymaga całego Required CI po przejrzanej integracji
optymalizacji workflow przygotowywanych w osobnej sesji.

Do zamknięcia AI12 potrzeba kwalifikacji adaptera na rzeczywistej publikacji
przez HTTP i model chat, pozostałych źródeł biznesowych/ML, fizycznego mappingu
stockout/inventory, natywnych powiązań model status/ryzyka dla sugestii,
przepływu AI10 outbox/v2 → API/UI z retry/deduplikacją, przeglądu etykiet
i konfiguracji oraz ograniczonego Sonnet/Titan na zaakceptowanym indeksie
AI11 w dostępnym budżecie. Test doubles nie zamykają tych zależności.
