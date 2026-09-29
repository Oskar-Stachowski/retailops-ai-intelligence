# Odbiór AI 04.8 — evidence runu i handoff

Data: 2026-09-29. Branch `ai/04-01-task-calendar`. Kod eksportu:
`328d6ee112093796226a6505672d696f78da58dc`. Źródło RetailOps:
`66ea303bbbe95ecb74cfbdab91b92afd13220e9f`.
[Kontrakt i polecenia](../forecast-run.md) opisują pakiet i granice AI 05.

Run: `run-77a5e7dbff215895baac1709ded1f73f` w
`data/generated/forecast-runs/`. Ma **557 plików / 248 675 504 B**
z osobnymi sumami SHA-256. Manifest SHA-256:
`e21fcf75abe4b3465c497e289e4a01636469464d53cef92a58e0528600893c4a`.
Eksport trwał od `2026-09-29T17:22:54.398963Z` do
`2026-09-29T17:22:55.585229Z`; to czasy archiwizacji, nie dawnego treningu.
`run_status=export_succeeded`, `forecast_model_status=not_ready`.

| Rodzic | Immutable ID |
|---|---|
| source | `source-sha256-94829460645140b79a6f68e87194f74d2c8e55392e2702a9fecbe6b771116d22` |
| curated | `curated-sha256-aeedd1a49f75eb27b687b328b92e23fee5b8cfa7c2520b2ff5219a8a74bd748a` |
| features | `features-sha256-e7992553d0d3b67f00b5f16e8e0ace55bb88fc3f3d023135bfcd70538e0fdbaf` |
| backtest | `forecast-backtest-sha256-0f8e578af20d95d6d6062e7d00f13b30b4522b30b1bb5ab68e9371483a5f3f22` |
| quality | `forecast-quality-sha256-66143185382f4f2f9353c0383b2661ac37e14293431959273878684014b6aab6` |

Archiwum zachowuje pełne 301 140 predykcji backtestu, trzy foldy,
pipeline’y RF/HGB i preprocessing, 1624 metryki segmentowe, 294 kalibracje,
76 104 predykcje przedziałowe, konfigurację i model card. Zbiorczy
manifest wskazuje dokładne label/split/comparison IDs, seed 42 dla danych
i modelu, lockfile, rewizje kodu, checksumy oraz wynik bramki.
`signature.json` i `input_example.json` pokazują schemat bez odczytu
final testu. [Raport pełnej weryfikacji](04-08-temporal.json) potwierdza
lineage i odtworzenie raportów z kopii rodziców.

Jakość pozostaje **`not_ready`: 145 passed, 79 failed, 8 not_ready**.
Pusty koszyk historycznego wolumenu zero, regresja drugiego folda,
niski wolumen i niedostateczne pokrycie przedziałów w wysokim wolumenie
pozostają otwarte. Pełne uzasadnienie i wartości są w
[odbiorze 04.7](04-07-quality.md). Run nie nadaje aliasu champion,
nie rejestruje wersji, nie włącza batch ani serving. Nie było nowych fitów,
AWS, MLflow, zmian DB/API ani otwarcia portfolio final testu.

Handoff do AI 05 zachowuje `run_id`, source/feature/label/split IDs,
oryginalne czasy **eksportu**, polityki, metryki, karty, pipeline’y i sumy.
Nowy czas `imported_at` należy zapisać osobno. Pola treningowego run ID oraz
rzeczywistych czasów treningu są `null`, bo wcześniejsze artefakty ich
nie rejestrowały. Import nie może przedstawiać ich jako runu treningowego
MLflow. AI 05 doda właściwy store, registry, decyzję reviewer/gate i serving,
ale tylko po osobnej kwalifikacji jakości.

## Weryfikacja

`run-export` utworzył pakiet, a `run-verify` potwierdził checksums wszystkich
557 plików i powiązania source → curated → features → split/comparison →
backtest → quality. Powtórny eksport z tym samym commitem i rodzicami zwrócił
to samo run ID; SHA-256 manifestu przed i po pozostał
`e21fcf75abe4b3465c497e289e4a01636469464d53cef92a58e0528600893c4a`.
Nie nadpisano opublikowanego katalogu. [Raport temporalny](04-08-temporal.json)
zapisuje 557 receipts, 248 675 504 B, parent IDs, seed i bramki.

[Odłączony wheel](04-08-wheel.json) załadowany przez Python `-I` poza
checkoutem ponownie zweryfikował pełny pakiet i jego raporty. Moduł pochodził
z wheel, nie z lokalnych plików źródłowych; schema manifestu była obecna
w dystrybucji. Wheel SHA-256:
`65782e33506f839569efaaca05126ba13aa0ea4f6e52e06abadb0ac9aa6bec1c`.

Pełna regresja: **898 passed / 556,07 s**. Ruff i format: 252 pliki,
mypy strict: 152 pliki. Schematy, dokumentacja/linki, komponent runu
oraz wheel/sdist przechodzą. Pozostałe bramki handoff/import/curated,
kalendarza, cech, manifestów, baseline’ów, modeli, backtestu, jakości
i konfiguracja Compose również przechodzą. Gitleaks: 80 commitów i
592,49 MB plików roboczych bez wykrytych sekretów. Brak nowych fitów
i wywołań AWS; etap nie uruchamiał usług ani zdalnego Required CI.
