# Odbiór AI 12 — chat Bedrock i circuit breaker

Data: **2026-09-29**. Zakres: lokalny adapter prawdziwego providera i przygotowane
ograniczone wykonanie. [Instrukcja](../agent-bedrock.md),
[metadane odbioru](12-bedrock.json), [propozycja](12-bedrock-proposal.json),
[źródło cen](12-bedrock-pricing.json).

## Zmiana

Converse używa przypiętego modelu/config, system prompts oraz dokładnego
JSON Schema draftu. CountTokens otrzymuje identyczne system/messages przed
rezerwacją kosztu i paid inference. SDK nie ma ukrytych retry. Błędy providera
mają bezpieczne kody; model nie dostaje native SDK tools. Positive provider
rates są obowiązkowe. Niezweryfikowany cross-region profile jest odrzucany.

Circuit breaker utrzymuje limit SDK także po anulowaniu coroutine. Spóźniony
wynik nie zamyka nowszego circuit. Udany token preflight nie zeruje awarii
chat. Auth/schema nie uruchamiają retry; throttle/transient zużywają wspólny
budżet retry/token/cost. Invalid JSON z prawidłowym usage dopuszcza jedną naprawę.

CLI wybiera sześć pytań z istniejącego golden, sprawdza zamrożone oracles
i pełną bramkę offline. Profil nie może podmienić polityki, etykiet, źródeł
lub dopisać pytania spoza zestawu. Domyślny wynik to `not_run`, bez klienta AWS.
Jawne wykonanie wymaga limitu zgodnego z config i nowego prywatnego raportu.
Checkpoint na dysku poprzedza jakiekolwiek AWS. Raport przechowuje odpowiedzi,
cytaty, safe traces, liczniki i koszt; błędy zależności zatrzymują resztę testu.

## Walidacja

Nowe testy obejmują zgodność parametrów z zainstalowanymi schematami botocore,
identyczność liczonego i wysyłanego inputu, niewiarygodny usage/cache/native
tool call, limity, redakcję wyjątków, retry, circuit recovery/fencing, anulowany
SDK call, policy/case binding, porównanie odpowiedzi z oracles oraz CLI bez AWS.
Transport jest sprawdzony na testowych odpowiednikach SDK, nie na prawdziwym
modelu. Wyniki pełnej regresji, golden i kontroli pakietu podaje JSON odbioru.

## Bieżący odbiór

Ten dokument opisuje historyczny zakres implementacji transportu. Aktualne
modele, zgoda kosztowa, wykonane wywołania i otwarte bramki znajdują się w
[odbiorze rzeczywistych prób](12-bedrock-real.md). Powiązane wcześniejsze
propozycja/cennik/wheel są dowodami poprzedniego zakresu, nie konfiguracją
bieżącego testu. AI 12 pozostaje otwarty.
