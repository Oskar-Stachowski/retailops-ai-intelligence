# Chat Bedrock i ograniczony test

AI 12 ma adapter Converse, kontrolę dostępu konta i zweryfikowane europejskie
profile Haiku 4.5 oraz Sonnet 4.6. [Bieżący wynik i koszt](evidence/12-bedrock-real.md)
odnoszą się do rzeczywistych wywołań modelu. Standardowe Assistant API wymaga
jeszcze planner/resolvera i podłączenia źródeł; sam test CLI go nie uruchamia.

## Modele i zakres

| Wariant | Model bazowy | Profil inference | Input / output za milion tokenów |
| --- | --- | --- | --- |
| Podstawowy | `anthropic.claude-haiku-4-5-20251001-v1:0` | `eu.anthropic.claude-haiku-4-5-20251001-v1:0` | 1,10 / 5,50 USD |
| Porównanie | `anthropic.claude-sonnet-4-6` | `eu.anthropic.claude-sonnet-4-6` | 3,30 / 16,50 USD |

Oba warianty startują w `eu-central-1`, z temperaturą 0. Adapter weryfikuje
przez AWS aktywny systemowy profil, dokładny model i zamkniętą listę regionów
UE: Frankfurt, Sztokholm, Irlandia, Paryż, Mediolan i Hiszpania. Odrzuca obcy
model, profil globalny, region spoza listy i ARN z identyfikatorem konta.
CountTokens używa modelu bazowego; Converse używa sprawdzonego profilu EU.
[Dowód dostępności profili i CountTokens](evidence/12-bedrock-preflight.json).
Nova Lite nie obsługuje wymaganego CountTokens i nie jest bieżącym wariantem.

[Konfiguracja Haiku](../agent/graph.bedrock-smoke.v1.json) oraz
[konfiguracja Sonnet](../agent/graph.sonnet-smoke.v1.json) używają tych samych
promptów, walidatorów, sześciu pytań i zamrożonych oracles. Sprawdzają sprzedaż,
zapas, porównanie okresów, cytowaną dokumentację, odmowę zamówienia i cudzy
scope. Ostatnie dwa przypadki kontroluje serwer bez modelu.

**Dane narzędzi i wyniki retrieval są fixtures.** Test mierzy rzeczywisty chat
na zamrożonych dowodach, nie ponowny odbiór embeddings/RAG, realnych źródeł ML
ani AI 10. Nie zamyka AI 12. Etykiety i progi nie są dopasowywane do modelu.

Bieżący kandydat do dalszej kwalifikacji to **Sonnet 4.6**. Ostatni wynik to
5/6, z otwartym problemem semantyki fixture dokumentacji. Haiku pozostaje
profilem porównawczym; nie jest modelem zakwalifikowanym do runtime.

## Dostęp, tokeny i koszt

CLI przed inference sprawdza formularz Anthropic, umowę, autoryzację,
entitlement i dostępność regionu. Brak lub nieznany status kończy się raportem
`blocked`, bez CountTokens i Converse. Kontrola niczego nie subskrybuje i nie
zmienia IAM. [Dostęp konta](agent-bedrock-access.md) opisuje wymagania wznowienia.

Każde Converse poprzedza CountTokens z tymi samymi system/messages. Rezerwujemy
policzone wejście oraz maksymalne wyjście. Poprawny usage może być niższy od
rezerwy: w rzeczywistym Haiku obserwowano 4908 policzonych i 4891 naliczonych
tokenów. Rozliczamy dodatni rzeczywisty input nieprzekraczający rezerwy,
wyjście w limicie i zgodną sumę. Większy input, cache, native tool call lub
nieznana odpowiedź są odrzucane, z zachowaniem rezerwy. Diagnostyka pokazuje
wyłącznie bezpieczne kody i liczniki, bez promptów, treści wyjątków i formularza.

SDK ma jedną próbę; sesja dopuszcza do dwóch retry wyłącznie dla przejściowych
awarii/throttle i jedną naprawę JSON. Wszystko zużywa wspólny budżet. Run ma
45 s, provider 20 s, do 16 000 input i 3000 output łącznie, do 1500 output na
próbę i najwyżej sześć prób. Circuit breaker oraz dwa miejsca dla operacji SDK
obejmują też operacje nadal trwające po anulowaniu coroutine.

Właściciel zatwierdził **1,00 USD łącznie na obecną serię testów i porównanie**.
To nie jest 1 USD na każdą próbę. Haiku ma cap smoke 0,15 USD, Sonnet 0,25 USD.
Limit kosztu całego smoke pozostaje nadrzędny wobec sumy limitów tokenów pytań.
Run ma limit odpowiednio 0,04 i 0,11 USD. [Cennik i SKU](evidence/12-bedrock-real-pricing.json)
pochodzą z publikacji AWS z 2026-09-28. Koszt dotyczy modelu według tych stawek,
nie całego rachunku konta. Nie utworzono provisioned ani reserved throughput.

SmokeBudget łączy sesje jednego procesu. Rejestr w [odbiorze](evidence/12-bedrock-real.md)
łączy wszystkie próby tej serii, także nieudane i niepewne rezerwacje. Przed
kolejnym procesem trzeba odjąć je od 1 USD; samo CLI nie zapewnia limitu między
procesami. Przerwane wykonanie bez końcowego kosztu zachowuje pełny cap próby.

## Uruchomienie

`make bedrock-smoke` sprawdza pełny offline golden i wiązania release, a potem
pokazuje `not_run`, bez klienta AWS. Jawny test Haiku po sprawdzeniu pozostałego
budżetu:

```sh
make bedrock-smoke BEDROCK_ARGS='--execute --max-cost-usd 0.15 --output .local/bedrock-haiku-next.json'
```

Dla Sonnet użyć tego samego polecenia CLI z parametrami
`--config agent/graph.sonnet-smoke.v1.json`,
`--profile agent/sonnet-smoke.v1.json`, `--max-cost-usd 0.25` oraz nowym outputem.
Pozostałe parametry są identyczne jak w celu `bedrock-smoke` w Makefile.

Katalog `.local` musi istnieć. Raport ma 0600, nową nazwę i trwały checkpoint
przed AWS. Istniejącego raportu nie nadpisujemy. `execution_started` po przerwaniu
nie dowodzi sukcesu ani nie zeruje kosztu. Błąd zależności/deadline/budżetu
zatrzymuje następne przypadki. Konsola pokazuje tylko podsumowanie.
Poświadczenia pochodzą z AWS default chain lub `--aws-profile`.

Odczytowe uprawnienia preflight obejmują `bedrock:GetUseCaseForModelAccess`,
`bedrock:GetFoundationModelAvailability` i `bedrock:GetInferenceProfile`.
Wykonanie wymaga `bedrock:CountTokens` dla modelu oraz `bedrock:InvokeModel`
dla profilu i jego modeli docelowych. Wspólne admission replik opisuje
[Assistant API](assistant-api.md); test lokalny nie jest wdrożeniem chmurowym.

## Źródła

- [Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
- [CountTokens](https://docs.aws.amazon.com/bedrock/latest/userguide/count-tokens.html)
- [Dostęp do modeli i formularz](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
- [Geograficzne profile](https://docs.aws.amazon.com/bedrock/latest/userguide/geographic-cross-region-inference.html)
- [Regionalny cennik modeli](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrockFoundationModels/current/eu-central-1/index.json)
