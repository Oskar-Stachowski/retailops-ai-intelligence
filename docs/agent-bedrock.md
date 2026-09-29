# Chat Bedrock i ograniczony test

**AI 12 · adapter wykonany, rzeczywisty test nieuruchomiony.**
[Odbiór zakresu](evidence/12-bedrock.md), [propozycja testu](evidence/12-bedrock-proposal.json).
Podłączenie standardowego Assistant API do rzeczywistych pytań, resolvera źródeł
i danych biznesowych pozostaje osobnym zakresem. Ten adapter sam go nie uruchamia.

## Konfiguracja proponowanego testu

[Manifest grafu](../agent/graph.bedrock-smoke.v1.json) przypina model
`amazon.nova-lite-v1:0`, `eu-north-1`, temperaturę 0 oraz prompty i walidatory
używane przez zamrożony profil offline. To propozycja pierwszego testu integracji;
jakość tego modelu w RetailOps nie jest jeszcze potwierdzona. Adapter obsługuje
inference w jednym regionie. Niezweryfikowany cross-region inference profile
jest odrzucany przed utworzeniem klienta AWS.

[Profil smoke](../agent/bedrock-smoke.v1.json) wybiera sześć istniejących przypadków:
sprzedaż, zapas, porównanie okresów, cytowaną dokumentację, odmowę zamówienia
i cudzy scope. Ostatnie dwa kontroluje serwer bez wywołania modelu. Pytania,
oczekiwane odpowiedzi i etykiety pochodzą z zamrożonego golden; nie są
przepisywane z odpowiedzi modelu ani aktualnego katalogu dowodów.

**Narzędzia i wyniki wyszukiwania w tym teście są fixtures.** Rzeczywisty jest
wyłącznie wywoływany chat, kiedy jawnie uruchomimy wariant płatny. Test nie
mierzy ponownie retrieval ani embeddings z AI 11 i nie odbiera danych ML/AI 10.
Nie zamyka AI 12, nawet jeśli wszystkie sześć przypadków przejdzie.

## Tokeny, koszt i awarie

Przed każdą płatną próbą `Converse` adapter wysyła `CountTokens` dla dokładnie
tych samych `system` i `messages`. Brak wsparcia CountTokens, błąd dostępu,
timeout albo niewiarygodny licznik blokuje inference; nie używamy przybliżenia
bajtów jako zgody na płatne wywołanie. Licznik przekraczający budżet tokenów
również blokuje inference. CountTokens nie jest dodatkowym chat wywołaniem.

SDK ma jedną próbę. Retry realizuje istniejąca sesja agenta, wyłącznie dla
throttle/transient, najwyżej dwa razy we wspólnym budżecie. Auth/schema nie
uruchamia retry. Awaria preflight CountTokens kończy bieżący run. Łączny
deadline to 45 s; provider timeout w tym profilu to 20 s. Run ma do 12 000 input
i 1500 output tokens łącznie, do 1000 output na pojedynczą próbę i do sześciu
chat prób. Jedna naprawa także zużywa ten budżet.

Zweryfikowany [cennik AWS dla Sztokholmu](evidence/12-bedrock-pricing.json)
opublikowany 2026-09-28 podaje **0,065 USD / milion input tokens** oraz
**0,26 USD / milion output tokens** dla on-demand Nova Lite. Budżet konfiguracji
to 0,002 USD na run i **proponowane 0,05 USD na cały smoke**. Przy pełnych
limitach tokenów jeden run kosztuje najwyżej szacowane 0,00117 USD, a sześć
runów 0,00702 USD. To koszt wywołań modelu według przypiętych stawek, nie limit
całego rachunku konta AWS, podatków lub innych usług. Przed rzeczywistym testem
trzeba ustalić z użytkownikiem kwotę i zachować aktualne wiązanie cennika.

Wspólny SmokeBudget obejmuje wszystkie sesje jednego testu. Timeout, anulowanie
lub niewiarygodny usage zachowują pełną rezerwację. Poprawny usage rozlicza
rzeczywiście obserwowane tokeny, również gdy JSON wymaga naprawy. Nieznana
odpowiedź, native tool call, nietekstowa treść, niezgodny token count i cache
usage są odrzucane. Model nie otrzymuje native narzędzi SDK.

Circuit breaker jest wspólny dla sesji używających jednej instancji providera:
trzy kolejne awarie, 30 s przerwy i jedna próba odzyskania. Udany CountTokens
nie zeruje awarii inference. Otwarcie blokuje dalsze SDK wywołania. Limit dwóch
operacji SDK w tle obejmuje także anulowane operacje aż do rzeczywistego końca.
Spóźniony sukces nie zamyka circuit otwartego przez timeout lub nowszą awarię.
To lokalny mechanizm providera; wspólne admission replik opisuje
[Assistant API](assistant-api.md).

## Uruchomienie

`make bedrock-smoke` najpierw sprawdza pełny offline golden i wiązania release,
a następnie pokazuje propozycję `not_run`. Nie tworzy klienta AWS i niczego nie
wywołuje. Profile/schema/code drift blokuje test przed utworzeniem klienta.

Po ustaleniu limitu 0,05 USD można uruchomić:

```sh
make bedrock-smoke BEDROCK_ARGS='--execute --max-cost-usd 0.05 --output .local/bedrock-smoke-run-1.json'
```

Katalog `.local` musi istnieć; nazwa raportu musi być nowa. Poświadczenia
pochodzą z AWS default chain albo jawnego `--aws-profile`; nie zapisujemy ich
w repozytorium. Potrzebne są `bedrock:CountTokens` dla foundation model oraz
`bedrock:InvokeModel` dla Converse w wybranym regionie. Nie uruchamiamy chmurowej
ewaluacji ani provisioned throughput. Dostęp konta i obsługa CountTokens przez
model muszą zostać potwierdzone w rzeczywistym teście.

Raport ma uprawnienia 0600. CLI rezerwuje plik przed AWS, zapisuje trwały
checkpoint rozpoczęcia, a potem wynik, odpowiedzi/cytaty, bezpieczne traces,
liczniki wywołań i szacowany lub zarezerwowany koszt. Konsola pokazuje tylko
podsumowanie. Istniejący raport nie jest nadpisywany. Przerwany proces pozostawia
`execution_started` z nieznanym wykonaniem AWS; taki zapis nie oznacza sukcesu
ani pozwolenia na automatyczne powtórzenie. Błąd providera/deadline/budżetu
zatrzymuje pozostałe płatne przypadki. Failed walidacji odpowiedzi pozostaje
failed; nie zmieniamy etykiet lub progów, aby model przeszedł test.

## Źródła AWS sprawdzone podczas implementacji

- [Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
  opisuje system/messages, usage, stop reasons i uprawnienie InvokeModel.
- [CountTokens](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_CountTokens.html)
  i [instrukcja liczenia tokenów](https://docs.aws.amazon.com/bedrock/latest/userguide/count-tokens.html)
  opisują dokładne wejście, obsługę zależną od modelu i brak opłaty za liczenie.
- [Nova Lite](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-lite.html)
  opisuje Converse i regionalną dostępność; nie dowodzi dostępu konkretnego konta.
- [Regionalny cennik AWS](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/eu-north-1/index.json)
  jest źródłem stawek i SKU zapisanych w evidence.
