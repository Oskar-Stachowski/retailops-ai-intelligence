# Konfiguracja i testowy provider AI 12

Ten zakres przygotowuje punkt 2 AI 12 bez zależności od AI 10. Wprowadza
interfejs providera, deterministyczny `ScriptedChatProvider`, wersjonowane
prompty i kontrolę wywołań modelu. Jest lokalnym przygotowaniem grafu;
nie jest odbiorem rzeczywistego chat Bedrock ani zamknięciem AI 12.
[Aktualny status](STATUS.md), [narzędzia](agent-tools.md),
[odbiór zakresu](evidence/12-chat.md).

## Konfiguracja i uruchomienie kontroli

[agent/chat.fake.native-v12.v1.json](../agent/chat.fake.native-v12.v1.json) jest konfiguracją maszynową.
Wskazuje model `scripted-chat-v1` w regionie `offline`, parametry generacji,
budżet i syntetyczne stawki zerowe. Przypina zatwierdzony indeks AI 11,
konfigurację Titan V2 i retrieval, checksum schematów narzędzi/odpowiedzi
oraz sześć promptów z pakietu: policy, tool selection, evidence, response,
refusal i examples. Embeddings w tej konfiguracji opisują przestrzeń wiedzy;
samo sprawdzenie konfiguracji nie wywołuje AWS ani retrieval.

```bash
uv run --locked retailops-ai agent-config-check agent/chat.fake.native-v12.v1.json
make contracts-check
uv run --locked pytest tests/test_agent_chat.py tests/test_agent_tools.py
```

CLI zwraca `status`, `provider`, `config_id` i `provider_invoked=false`.
Potwierdza zgodność lokalnych plików i schematów. Nie potwierdza dostępności
modelu/indeksu, uprawnień AWS ani jakości odpowiedzi. Błędna konfiguracja
kończy się kodem 2 i `agent_chat_config_invalid`, bez jej treści.

`config_id` to SHA-256 kanonicznej konfiguracji. Zmiana modelu, parametrów,
promptu, schematu, retrieval, indeksu lub budżetu zmienia identyfikator.
Loader sprawdza checksumy względem bieżącego kodu i zasobów pakietu,
odrzuca nieznane pola, duplikaty kluczy JSON, NaN i pliki ponad 64 KiB.
Prompty mają zamknięte nazwy i ścieżki; tekst modelu nie wybiera pliku.
Załadowana konfiguracja jest osobnym, niezmiennym snapshotem.

`make contracts` generuje [schematy i przykłady](../contracts/agent/v1/)
oraz [manifest powiązań](../contracts/agent/v1/chat-bindings.v1.json).
Nie aktualizuje automatycznie checksum zatwierdzonej konfiguracji. Po zmianie
promptu/schematu należy jawnie przeglądnąć i zaktualizować konfigurację oraz
powtórzyć kontrolę. `graph_version=pregraph-provider-v1` oznacza ten zakres,
nie gotowy graf LangGraph ani końcowy manifest agent release.

## Granica wykonania

Serwer tworzy `ToolSession` z poświadczeń i jedną powiązaną `ChatSession`.
Provider testowy wymaga `environment=test` oraz jawnego `allow_fixtures=True`.
Kolejna sesja modelu nie może zacząć nowego budżetu w tym samym runie narzędzi.
Model proponuje `PlanDraft` albo `AnswerDraft`, bez principal, trace czy
wersji przypinanych przez serwer. Plan podlega istniejącej walidacji
capabilities, całego scope, dat i limitów. Zwrócenie planu nie wykonuje narzędzi.

Kontekst pochodzi wyłącznie z zaakceptowanych wyników tej sesji narzędzi.
Każdy wynik ma referencję do checksum treści. Wiedza musi odpowiadać
przypiętemu indeksowi oraz konfiguracji retrieval. Cytat zachowuje repository,
commit, path, heading, chunk ID, status i source_ref pobranego fragmentu.
Podmiana któregokolwiek pola, niepobrany cytat, obcy source_ref, zmyślone
as-of/freshness lub użycie `no_data` jako faktu biznesowego są odrzucane.
Snapshot użyty w request pozostaje podstawą kontroli odpowiedzi nawet wtedy,
gdy równolegle zakończy się inne narzędzie.

Pytanie oraz wyniki narzędzi i dokumenty są oddzielone od promptów policy.
Referencje mają `content_trust=untrusted_reference`; instrukcja w dokumencie
lub pytaniu nie nadaje praw ani narzędzi. Fake działa według dokładnego
checksum request, bez interpretacji języka naturalnego. Odtwarza poprawną
odpowiedź, throttle, transient failure, auth/schema failure i timeout;
skrypt może również zwrócić błędny JSON, fałszywy cytat lub zabronione narzędzie.

## Budżet, retry i naprawa

| Limit domyślny | Wartość |
|---|---|
| Wspólny deadline narzędzi i rozmowy | 45 s od utworzenia ToolSession |
| Wywołania modelu, łącznie z retry/repair | 6 |
| Tokeny input / output na run | 12 000 / 1 500 |
| Output na pojedyncze wywołanie | 400 |
| Timeout providera | 5 s, ograniczony pozostałym deadline |
| Retry throttle/transient w całym runie | najwyżej 2, backoff z jitter |
| Naprawa schematu/referencji | najwyżej 1, ten sam question i snapshot |
| Limit kosztu run / wspólnego smoke | 0,02 / 0,05 USD |

Przed wywołaniem serwer rezerwuje konserwatywny input i maksymalny output
oraz odpowiadający im koszt. Poprawny licznik usage zwalnia niewykorzystaną
rezerwację. Timeout, anulowanie, wyjątek lub niewiarygodne usage pozostawiają
pełną rezerwację. Także niepoprawny draft z poprawnym usage zużywa swoją
próbę i tokeny. Retry i repair korzystają z pozostałego budżetu; nie resetują
deadline. `SmokeBudget` współdzieli limit kosztu pomiędzy sesjami jednego
kontrolowanego smoke w procesie. Nie jest rozproszonym licznikiem wielu replik.

Fake szacuje input przez konserwatywną liczbę bajtów własnego payloadu.
Duży kontekst może więc zostać odrzucony przed wywołaniem. To testowy limit,
nie tokenizer ani pomiar tokenów Bedrock. Stawki zerowe nie są cennikiem AWS;
testy kosztu używają osobnych syntetycznych stawek dodatnich.

Auth/schema failure nie powoduje retry, a timeout kończy próbę. Naprawa
dostaje kanoniczny kod błędu i ten sam kontekst; nie dostaje surowego błędnego
outputu. Nieudana naprawa kończy się kontrolowanym błędem.

Audit w pamięci zawiera config ID, fazę, wynik, kanoniczny kod, czas,
tokeny i szacowany koszt. Nie zapisuje question, raw response, exception text,
sekretów ani ukrytego toku rozumowania. [Assistant API](assistant-api.md) ma
osobny trwały trace i kontrakt HTTP. Kody domenowe to `budget_exceeded`, `deadline_exceeded`,
`provider_unavailable`, `invalid_output`, `invalid_evidence`,
`unauthorized_tool` i `invalid_repair`.

## Co pozostaje

Draft potwierdza strukturę i powiązanie referencji. Nie potwierdza jeszcze,
że liczba lub zdanie odpowiada znaczeniu źródła. Numeric faithfulness,
groundedness, dobór narzędzi, reguły statusu „wdrożono”, zatwierdzone formuły
i deterministyczna polityka sugestii wymagają grafu i ewaluacji odpowiedzi.
W tym profilu obliczenia i modelowe `recommended_actions` są odrzucane.

[Profil grafu](agent-graph.md) ma odrębny manifest i prompty v3 oraz kontrolę
kanonicznych faktów i obliczeń. Powyższy profil pregraph z promptami v1 zachowuje
wcześniejsze ograniczenia. [Reguły i golden](agent-evaluation.md) obejmują
kanoniczne odpowiedzi i kandydatów na fixtures. [Assistant API](assistant-api.md)
dodaje admission i trwałe wyniki. [Adapter Bedrock](agent-bedrock.md) ma
circuit breaker, CountTokens i przypięte profile/cenniki Haiku oraz Sonnet.
[Bieżący odbiór](evidence/12-bedrock-real.md) podaje rzeczywiste próby i ich granice. Źródła biznesowe
oraz producent sugestii/outbox/E2E wymagają dalszych etapów, w tym AI 10.
