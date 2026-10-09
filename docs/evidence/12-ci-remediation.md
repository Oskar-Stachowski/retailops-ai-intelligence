# AI12 — zgodność integracji i poprawki CI

**Status: in_progress.** [Receipt](12-ci-remediation.json),
[draft PR32](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/32).
Dowody pierwszego wznowienia w `12-resume.json` pozostają historyczne.

## Zakres

- Formatowanie czterech nowych lub połączonych plików zgodnie z Ruff.
- Lockfile osobnego środowiska TensorFlow zawiera teraz zależności LangGraph.
  `uv lock --check --offline` przechodzi dla 129 pakietów. Trening CPU,
  zapis artefaktu, MLflow i reload wykonuje wymagany izolowany runner CI.
- [Oryginalny lockfile kwalifikacji](../../environments/anomaly/README.md)
  wiąże się dokładnie z lock SHA w obu niezmienionych kapsułach AI07.
  Kontrola zgodności dopuszcza jawne rozszerzenie o `langgraph==1.2.12` po
  sprawdzeniu identyczności 70 dotychczasowych zewnętrznych package records,
  ich zależności, markerów i hashy dystrybucji. Inne zmiany zależności,
  metadanych root, Python/resolver settings lub brak oryginalnej referencji
  nadal blokują ponowny odbiór. Raport wiąże osobno lock treningowy i runtime.
- Unit tests workera prognoz używają jawnego środowiska pasującego do
  historycznego SQL-only stub release. Produkcyjna kontrola ścisłej równości
  pinów pozostaje bez zmian. Nowy test potwierdza, że rzeczywisty zmieniony
  lockfile blokuje historyczny release przed computation i publikacją.
- Release offline oraz trzy propozycje smoke `.resume.v2` wiążą nowy code hash.
  Manifesty `.resume.v1`, historyczne receipts, modele i golden labels
  pozostają niezmienione. Nie wykonano nowych wywołań AWS.

## Sprawdzone lokalnie

- 569/569 wybranych testów: 545 obejmuje routing, agenta, Assistant API,
  access, Bedrock stubs, kontrakt CI i odmowy niedozwolonych zmian lockfile;
  24 obejmuje publikację prognoz i wiring workera.
- Całe `make ci-checks` passed: Ruff, mypy (650 plików source/scripts),
  kontrola dokumentacji i bramek CI, forecast runtime, wszystkie kontrakty,
  ocena 50/50 przypadków i 36/36 krytycznych, build oraz Compose config.
- Wheel zawiera 532 pliki Python z tym samym code checksum co checkout
  oraz poprawne root/TensorFlow lockfiles. Gitleaks bieżącego drzewa passed.
- Oba oryginalne modele anomaly: signature/load/predict, pełne sześć
  zamrożonych zestawów wejścia i jakość passed. Porównanie bajtów potwierdza
  zachowanie modelu, konfiguracji, sygnatury, oracles, fit times i lock
  kwalifikacji treningowej. Nowe receipts dotyczą bieżącej zgodności.

Ten preflight nie zastępuje pełnego zdalnego OCI/PostgreSQL/MLflow ani testu
TensorFlow. Wszystkie dotychczasowe wymagane bramki CI są zachowane.
Następny zdalny wynik musi wskazywać opublikowany head PR32.

## Dalszy odbiór

Do READY pozostają rzeczywiste adaptery biznesowe/ML, fizyczne powiązania scope,
świeżość dziennych prognoz względem życia sugestii, sugestia rzeczywistego
agenta przez AI10 outbox/v2 do API/UI z retry/deduplication oraz przegląd
pytań/konfiguracji i bounded Sonnet/Titan na zaakceptowanym indeksie AI11.
Zdalne CI nie zastępuje tego odbioru runtime.

Praca odbyła się w osobnym worktree. Nie zmieniono sesji AI09/AI10, wspólnego
Compose ani baz; nie uruchamiano lokalnego treningu ani pełnych eksportów Source.
