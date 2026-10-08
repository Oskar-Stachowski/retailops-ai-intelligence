# AI12 — punkt wznowienia przed płatną kwalifikacją

Przygotowanie nie uruchamia inference ani embeddings. Bieżący plan to
[kampania v3](evidence/12-prepaid-test-plan-v3.json), a pełne etykiety i oracles
znajdują się w [pakiecie przeglądu v3](evidence/12-prepaid-label-review-packet-v3.json).
Opublikowane rodziny `.prepaid.v1` i `.prepaid.v2` pozostają niezmienione.
Rodzina `.prepaid.v3` dodaje chronione odczyty rekomendacji i proponowane
równoważne źródła instrukcji startu w niezmienionym indeksie AI11; zmiana kodu po jej publikacji
wymaga kolejnego identyfikatora konfiguracji i osobnego odbioru.

## Co można odtworzyć bez AWS

```sh
make bootstrap ci-checks
make agent-evaluate
make bedrock-smoke
```

Ostatnie polecenie pokazuje `not_run`. Pełny odbiór mechaniki Source wykonuje
[osobny workflow](../.github/workflows/ai12-source-prepaid.yml) bez AWS.
Wspólnych usług Docker/Compose nie trzeba uruchamiać. Workflow używa
przypiętego Source `5c05445e8105c378107099785bae7355388ed7e7` i własnych usług.

Pięć przygotowanych propozycji Sonnet obejmuje 41 naturalnych przypadków
z golden 50. Pozostałe dziewięć wymusza błędną lub złośliwą odpowiedź providera;
ich testy serwerowe pozostają w kompletnym golden i suite bezpieczeństwa.
Nie zmieniono etykiet ani progów, żeby uzyskać dodatni wynik modelu.

Przykład odtworzenia pierwszej propozycji, nadal bez AWS:

```sh
uv run --locked --extra snapshot --extra forecast retailops-ai bedrock-smoke \
  --config agent/graph.sonnet-qualification.prepaid.v3.json \
  --profile agent/sonnet-qualification-1.prepaid.v3.json \
  --offline-config agent/graph.evaluate.fake.prepaid.v3.json \
  --golden agent/golden.canonical.v1.json \
  --release agent/evaluation-release.fake.prepaid.v3.json \
  --rag-golden knowledge/golden.semantic.v1.json --lock uv.lock
```

Kolejne propozycje używają profili `sonnet-qualification-2` do `-5`.
Każda ma osobny cap 1,00 USD; proponowany łączny cap wynosi 5,00 USD.
To propozycja, bez zgody na wydatki. Historyczny budżet 1,50 USD nie jest
zerowany: zapisane koszty/rezerwy 1,4017210 USD pozostawiają 0,0982790 USD.
Wyniki CLI dotyczą prawdziwego chatu na zamrożonych dowodach testowych.
Nie są odbiorem natywnych danych ani rzeczywistego RAG.

## Warunki przed wznowieniem płatnych wywołań

1. Niezależnie ocenić 50 pytań, 26 tras oraz reguły dokumentowe. Brak takiego
   odbioru pozostaje jawny; AI11 przyjęło inny zestaw retrieval. Utworzyć nowy
   zaakceptowany artefakt tras z jego własnym ID i receipt, zachowując propozycje.
2. Uzyskać nową zgodę na koszt całej kampanii. Zachować wspólny trwały rejestr
   wszystkich procesów, także nieudanych i przerwanych prób. CLI nie zastępuje
   limitu łącznego między procesami. Sprawdzić stawki, model i EU profile ponownie.
3. Dla osobnego testu natywnego zweryfikować bieżące Source/Curated/DQ/coverage,
   aktywny kwalifikowany indeks AI11 i właściwy namespace modeli. Historyczny
   fixture nie upoważnia do odczytu bieżących okresów; funkcjonalny odbiór AI10
   z development v12 nie jest promocją do canonical ani odbiorem jakości.
4. Usunąć braki dowodów dokumentowych albo przyjąć niezależnie oceniony wynik
   `insufficient_evidence`. Odtworzone sześć pytań daje pełne pokrycie czterech po dodaniu proponowanych
   równoważnych źródeł instrukcji startu. Ranking top-5, wymagania i statusy
   pozostają niezmienione. Pytanie o kontrole przed commitem nadal nie znajduje
   wymaganych fragmentów w top-5;
   pytanie o zweryfikowane zabezpieczenie metryk nie ma odpowiedniego dowodu.
   Nie podnosić statusu `implemented` do `verified` ani osłabiać wymagań cytatów.
5. Jeśli potrzebny jest transport native-v2, uzyskać jego osobny odbiór po
   stronie Source. Bieżący E2E używa przyjętego v1 z jawnym fixture opt-in.
   Nie deklarować nieobserwowanego heartbeat ani Kafka lag.

[Natywny kandydat v3](../agent/native-bedrock-runtime.prepaid.proposed.v3.json)
przechodzi schema, lecz runtime celowo odrzuca jego proponowane trasy.
Zawiera osiem adapterów, rzeczywisty pin AI11 i osobny proponowany cap chatu
3,00 USD oraz rezerwę query embeddings 0,005 USD. Jest konfiguracją do
przeglądu i kwalifikacji na odtworzonych źródłach, bez poświadczeń i DSN.
Po zaakceptowaniu tras i bieżących źródeł tworzy się nową konfigurację prywatną
dla [runtime Bedrock](assistant-native-bedrock.md). Nie podłączać propozycji
do produkcyjnego serwisu.

Dopiero po spełnieniu powyższych warunków dla właściwego zakresu dopisuje się
do polecenia CLI `--execute --max-cost-usd 1.00 --output` oraz nową prywatną
nazwę pliku. Istniejących wyników nie nadpisywać. Kwalifikacja natywnego
Assistant wymaga osobnych żądań HTTP i trwałych receipts; sama kampania CLI
na fixtures jej nie zamyka. Przy błędzie zależności, deadline lub budżetu
wstrzymać kolejne próby i zachować koszt/rezerwę.

Stan AI12 pozostaje `in_progress`, a PR32 pozostaje draft do czasu odrębnego
odbioru tych bramek. Płatna kwalifikacja nie została rozpoczęta.
