# AI 05.7c — odbiór odczytu ocen

Data: **2026-09-30**. Branch `ai/05-mlflow-serving`; zakres lokalny.
**Odbiór HTTP/PostgreSQL i archiwalnych metryk przeszedł.**
[Runbook](../evaluations.md) podaje kontrakt, limity i pozostałe bramki.

## Przygotowanie historycznego dowodu

Wykonano `scripts/prepare_evaluation.py` dla istniejącego exportu
`run-77a5e7dbff215895baac1709ded1f73f` z worktree AI 04, z outputem
`reports/evaluation-evidence.json`. Nie zmieniono źródła, pinów ani modelu;
nie kopiowano archive/model binaries do Git. Przygotowanie zweryfikowało cały
export, receipts i lineage oraz odtworzyło MAE/WAPE z zapisanych predykcji,
memberships i etykiet istniejącym kalkulatorem/fold pooling AI 04.
Odtworzenie uwzględniło kompletność pięciu metod, wybrane strategie,
mature labels, ineligible keys i kontrolę raportowanych wartości.

ID oryginalnej oceny, hash projekcji i SHA stanu są w [raporcie](05-07-evaluations.json).
Projekcja ma hash
`f1be6d8e3ef19cc8e33fecbcb55a61bd30f6b17734773c66a29dca01098ece40`.
Scope obejmuje **8 produktów, 2 lokalizacje i 2 kanały** oraz **12012
memberships** dwóch ról, w tym wykluczone/cenzurowane klucze.
Udostępniono 14 grup: dwie role × siedem metod/strategii.

Dla `development_holdout:validation_selected` odtworzono 5400 eligible
predykcji, MAE **1,442419787664719**, WAPE **0,15750762058945003**,
sumę bezwzględnych błędów **7789,066853389482** i actuals **49452**.
Wynik pozostaje historyczną diagnostyką, ze statusem jakości **not_ready**.
Status bramek zachowano z oryginalnego raportu; nie kwalifikowano go ponownie
pod obecny runtime. Starsze code/lock pins pozostają jawne. Nie było nowego
treningu, recalibracji przedziałów, promocji ani otwarcia portfolio final test.

## PostgreSQL i HTTP

Wykonano:

```bash
.venv/bin/python scripts/check_evaluations.py \
  --historical-evidence reports/evaluation-evidence.json \
  --report reports/ai05-evaluations-acceptance.json
```

Kontroler utworzył projekt `retailops_ai_evaluations_359cca33fd` bez portów
hosta, zainstalował pakiet w obrazie
`sha256:8bc2df0c1c385a008a34fa9cdc9f261a3fe7d391289cc6257d37dafbe0465972`
i zastosował migrację **0013_forecast_evaluations**.
W bazie zapisano 37 jawnych synthetic ocen oraz 1 projekcję rzeczywistego
historycznego exportu. Nie utworzono MLflow runów/versions, model release'u
ani prognoz. Nie było wywołań MLflow i Bedrock.

HTTP/SQL potwierdziły:

- 401 bez credentials, 403 dla samego `forecast:run`, zamknięte/bounded filtry;
- whole-scope authorization przed count oraz 404 przy częściowym dostępie do raportu;
- filtry jakości przed count, bez cichego przepisywania globalnego scope;
- MAE/WAPE z kalkulatora, zachowanie failed/not_ready oraz serving=false;
- brak surowych raportów, URI i jednostek spoza scope w odpowiedzi;
- niezmienny widok mimo 33 obcych ocen oraz stabilne strony/409 po zmianie widoku;
- idempotentny import, odrzucenie błędnej identity oraz SQL odmowę UPDATE/DELETE;
- 503 bez metryk przy zmienionym MAE i dokładne odtworzenie dowodu;
- odczyt historycznego raportu przez HTTP wyłącznie dla pełnego autoryzowanego scope;
- identyczny pełny stan i widok ocen po SIGKILL/restart PostgreSQL.

Korupcja była zapisem właściciela własnej jednorazowej tabeli z transakcyjnym
wyłączeniem/włączeniem user triggers. `finally` przywrócił dokładny oryginał;
hash stanu i poprawny HTTP po przywróceniu były identyczne z pomiarem przed
uszkodzeniem. Normalne operacje nie wyłączają triggers. Kontroler usunął
własne kontenery, wolumeny i tag obrazu; trwałego stosu nie zmieniano.

## Kontrole lokalne

Przeszło 176 testów evaluations/catalog/forecast-read/access/CI/cleanup/
persistence oraz 300 testów publication/queue/runtime/inputs/HTTP/contracts/
Registry/lifecycle/MLflow. Dodatkowe 4 testy replay sprawdziły wykluczone
jednostki w scope, rehashed forged metric, brak metody i duplikat predykcji.
Łącznie **480 różnych testów**. Końcowy snapshot access i testy evaluations
ponowiono wraz z replay (19 przypadków).

Ruff/format, mypy (225 plików), snapshots kontraktów, linki docs/guard CI,
gitleaks oraz budowa wheel/sdist przeszły końcową kontrolę tego zakresu.
`evaluations-smoke` należy do persistence Required CI; test blokuje pominięcie
bramki. Cleanup chroni istniejące obrazy i usuwa własny tag także po błędzie.
Nie powtarzano treningu/ewaluacji jakości AI 04; replay dotyczył zapisanych
punktowych miar historycznego pakietu.

## Aktualne bramki

Odczyt jest gotowy dla zweryfikowanych, kompletnych historycznych raportów.
Częściowy grant nie otrzymuje globalnych metryk; nie ma API przeliczania
podzbioru danych. Zapisany wynik bramek nie jest nową kwalifikacją modelu.
Nie ma powiązania z nowym registered version lub potwierdzenia runtime/driftu.
Pozostają source watermark i pełna freshness prognoz, spójny qualified
handoff AI 04 z rzeczywistym batchem oraz zdalny Required CI brancha.
**AI 05 pozostaje otwarte.** Outbox i zdarzenia należą do AI 10.
