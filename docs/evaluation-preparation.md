# AI 09 — przygotowanie wspólnej oceny

Status całego AI 09: **in_progress**. [Przyrost 09.2](tensorflow-challenger.md)
dodaje rzeczywisty kompaktowy Keras CPU i osobne środowisko. Przyrost 09.1 przygotowuje plan;
nie uruchamia treningu TensorFlow ani końcowej oceny modeli. AI 07 i AI 08
wciąż przygotowują kwalifikację swoich danych i wyników.

## Co robi wykonywalny plan

[Wersjonowany kontrakt](../contracts/evaluation/v1/preparation.default.json)
przypina siedem dokumentów wymagań i reguł: ich repozytorium, historyczny commit,
ścieżkę, rozmiar i SHA-256. Początkowe piny porównano z dokładnymi blobami Git.
Runtime sprawdza odpowiadające im bajty w jawnie podanych katalogach; nie wymaga
przełączenia ich branchy i nie deklaruje sprawdzenia obecnego HEAD.

Plan zachowuje seedy danych **42, 137, 2026**, cztery wymagane scenariusze,
trzy zastosowania i horyzonty TensorFlow **1–14 dni**. Seed inicjalizacji modelu
jest osobnym polem. Preprocessing i vocabulary mają być dopasowane wyłącznie
na train danego folda; early stopping korzysta z development validation.
Ocena raportuje każdy seed/scenariusz przed agregacją. Wagi, krytyczne segmenty,
thresholdy i konkretne splity będą wymagały osobnego przypięcia przed final testem.
Wyjątki zaakceptowane dla AI 04 v12 nie są przenoszone na nową kampanię.

Początkowa propozycja dla kompaktowego Keras dense/direct multi-horizon to
co najwyżej 2 próby, 25 epok, 1 wątek CPU, 1200 s i 1024 MiB RSS drzewa
na próbę. Te liczby nie są zmierzonym odbiorem zasobów ani budżetem całego
portfolio. Wymuszanie limitów i rzeczywisty trening należą do następnego przyrostu.
TensorFlow będzie wymagać osobnego przypiętego środowiska; lock kampanii v12
pozostaje nienaruszony.

Manifest przygotowania wiąże plan, hash implementacji, istniejący lock zależności
i wersję Pythona. Ma własny identyfikator treści, zapis 0600 w katalogu 0700,
fsync i publikację bez nadpisania. Ponowienie weryfikuje istniejący plik.
Zmiana reguł, implementacji lub planu tworzy nową tożsamość; uszkodzenie
istniejącego pliku blokuje ponowienie. Manifest nie zawiera danych ani wyników ML.

## Polecenia offline

Uruchamiaj w odrębnym worktree z Python 3.11.15 i zależnościami z `uv.lock`.
Katalog RetailOps powinien zawierać bajty zgodne z przypiętymi specyfikacjami;
zmiana takich bajtów wymaga przeglądu i nowego planu. Przykład:

```bash
.venv/bin/python -m retailops_ai.evaluation_campaign.cli template

.venv/bin/python -m retailops_ai.evaluation_campaign.cli prepare \
  --retailops-repo /Users/oskarstachowski/retailops-cloud-native-platform \
  --ai-repo /private/tmp/retailops-ai09-evaluation-protocol \
  --output-root /private/tmp/ai09-preparation

.venv/bin/python -m retailops_ai.evaluation_campaign.cli verify \
  --retailops-repo /Users/oskarstachowski/retailops-cloud-native-platform \
  --ai-repo /private/tmp/retailops-ai09-evaluation-protocol \
  --preparation /private/tmp/ai09-preparation/ai09-preparation-sha256-HASH.json
```

`prepare` i `verify` zwracają 0 dla poprawnego przygotowania. `preflight`
przyjmuje te same argumenty co `verify`, wypisuje **evaluation_status=not_ready**
i zwraca **3**. Błąd kontraktu, źródła lub manifestu daje 2 i bezpieczny kod błędu.
Preflight nie otwiera danych ani holdoutów. Zgoda na final test lub promocję
ma w tym kontrakcie zawsze wartość false, również po przeliczeniu hashów.

## Wspólne klucze i dalszy zakres

`require_same_forecast_keys` porównuje pełne klucze produktu, miejsca sprzedaży,
kanału, origin, target date, horyzontu i konwencji czasu. Kolejność wejścia
nie zmienia wyniku, lecz pusty zbiór, duplikat lub pominięta obserwacja blokuje
porównanie. Limit 100 000 kluczy dotyczy tego pomocnika development; to nie jest
odbiór skali `ai-training`. Pełna kampania wymaga ograniczonego czytnika/indeksu
na dysku i tego samego zbioru ocenianych kluczy, bez obcięcia populacji do limitu.

Do kolejnych przyrostów pozostają:

- rzeczywisty Keras pipeline, train-only preprocessing, wspólny evaluator,
  izolowane środowisko, koszt oraz zapis/reload na CPU;
- konkretne source/curated/features/labels/splits po 06/07/08 i odbiory upstream;
- metryki i wersje thresholdów/kalibratorów, segmentów oraz wag agregacji;
- zatwierdzony final protocol, trwały audyt dostępu do holdoutu i runner;
- trzy seedy/scenariusze, robustness, trzy karty i decyzje lifecycle.

[Odbiór przygotowania](evidence/09-01-evaluation-preparation.md) rozróżnia
wykonane kontrole od tych przyszłych wymagań. Sam plan nie kwalifikuje modeli.
