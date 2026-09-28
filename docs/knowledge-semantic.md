# Semantyczny RAG — Etap 11

Rzeczywisty provider to Amazon Titan Text Embeddings V2 w `eu-north-1`.
[Konfiguracja embeddings](../knowledge/embeddings.bedrock.v1.json) przypina model,
1024 wymiary, normalizację, format float32 i transformację wejścia.
[Konfiguracja retrieval](../knowledge/retrieval.semantic.v1.json) wybiera fragmenty
według podobieństwa, z limitem dwóch fragmentów z dokumentu, pięciu wyników
i dotychczasowymi limitami kontekstu. [Aktualny status](STATUS.md).

Wejście dokumentu zawiera nagłówki sekcji rozdzielone LF, pusty wiersz i dokładną
treść fragmentu. Embedding checksum dotyczy tego wejścia. Cytat, zakres linii,
chunk ID i checksum źródła nadal odnoszą się do niezmienionych bajtów Git.
Różne nagłówki tej samej treści tworzą różne wektory/cache keys. Zmiana modelu,
regionu, wymiaru lub transformacji tworzy inną przestrzeń i nowy indeks.
Format wywołań: [dokumentacja AWS Titan V2](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-titan-embed-text.html).

## Przygotowanie i koszty

Zacznij od zatwierdzonego profilu opisanego w [profilach golden](knowledge-golden-jobs.md).
Polecenie ponownie odczytuje przypięte źródła Git i sprawdza zgody przed wywołaniem AWS:

```bash
uv run --locked retailops-ai knowledge-bedrock-prepare \
  --source-profile .local/rag/approved-golden-profile.json \
  --embedding-config knowledge/embeddings.bedrock.v1.json \
  --retrieval-config knowledge/retrieval.semantic.v1.json \
  --retailops-repo ../retailops-cloud-native-platform --ai-repo . \
  --cache-dir .local/rag/bedrock-cache \
  --output-dir .local/rag/semantic-release \
  --max-requests 550 --max-input-bytes 3000000
```

To jawne polecenie płatne; poświadczenia pochodzą ze standardowego łańcucha AWS
lub `--aws-profile`. Nie są zapisywane w profilu ani logach. Domyślne limity
to 550 żądań i 3 MB wejścia; bezwzględny limit implementacji to 1000 żądań/5 MB.
Pojedyncze wejście ma najwyżej 8000 bajtów UTF-8. Connect/read timeout to 5/20 s,
SDK nie ponawia wywołań; nieudana próba zużywa budżet. Limit żądań i tekstu
ogranicza pracę, ale nie jest limitem rachunku AWS w USD.

Cache ma katalog `0700` i pliki `0600`, sprawdza integralność, konfigurację,
wymiar i normę wektora. Pozwala wznowić przerwaną budowę bez ponownego płacenia
za zapisane odpowiedzi. Nowy `--output-dir` nie może już istnieć.
Dodaj `--offline`, aby wymagać kompletnego cache i wykluczyć wywołania modelu.

Powstają prywatne: candidate, golden, golden-approval, profile, golden-report
i preparation receipt. Nie commituj wektorów ani pełnego korpusu. Receipt
wskazuje źródłowy profil/zgodę oraz liczniki wywołań z danego uruchomienia.
Pipeline zachowuje dokładnie pytania, oczekiwane i zabronione sekcje, uprawnienia,
etykiety oraz progi. Przepisuje wyłącznie powiązania index/config/golden ID.
Zgoda ma `approved_pipeline` i zachowuje czas pierwotnej decyzji. Zmiana źródeł,
pytań lub progów wymaga odrębnego przeglądu; to polecenie ich nie modyfikuje.

## Run, kwalifikacja i aktywacja

`knowledge-profile-register` przyjmuje także profil
`approved_corpus_semantic_validation`. Dalej obowiązują istniejące endpointy
POST/GET `/api/v1/knowledge-index-runs`, grant `knowledge:index`, idempotency key
oraz polecenie `knowledge-index-work`. Rejestracja ponownie sprawdza źródła Git.
Worker odtwarza indeks z zapisanych wektorów i wszystkie wyniki golden z
wektorów pytań, bez AWS. Sukces daje kandydata. Niezaliczony próg daje
`failed/gate_failed`, z zachowanym raportem i bez outputu.

Po sukcesie, w środowisku z dostępem do odrębnej bazy AI:

```bash
retailops-ai index-qualify-semantic --run-id RUN_ID --env-file .local/runtime.env
retailops-ai index-current --lane retrieval --env-file .local/runtime.env
retailops-ai index-activate --index-id INDEX_ID --lane retrieval \
  --expected-generation GENERATION --request-id rag-change-32_HEX \
  --actor OWNER --env-file .local/runtime.env
```

Zastąp symbole prawdziwymi ID. Pierwsza generacja wejściowa to `0`; kolejne
odczytuj z current. Request ID zawiera dokładnie 32 małe cyfry hex po prefiksie.
Retry identycznego request ID zwraca niezmienny pin. Rollback używa
`index-rollback` z nowym request ID i obecną generacją, do wcześniej aktywnego
indeksu w tym samym środowisku/kanale. Szczegóły CAS i transakcji:
[lifecycle](knowledge-lifecycle.md).

Kwalifikacja odczytuje udany run, odtwarza pełny raport, sprawdza zgody,
decyzje podobieństwa i zamrożone progi. Zapisuje immutable release i qualification.
Migracja `0008_rag_semantic` wiąże release z raportem/runem/profilem w SQL;
nie dopuszcza użytkowej kwalifikacji fake ani kwalifikacji bez release.
Aktywacja pozostaje osobną operacją CLI. HTTP i narzędzia agenta jej nie wystawiają.
Downgrade schematu wymaga odtworzenia backupu; rollback indeksu nie cofa schematu.

## Wyszukiwanie

W `local` API odczytuje `retrieval`; `test` zachowuje domyślny kanał `offline_test`.
Pełny pin może służyć kontrolowanemu odczytowi obu kanałów. Parametry wyszukiwania
dla kanału użytkowego pochodzą z zatwierdzonego release danego pinu.
Caller nie może zmienić konfiguracji przez request.

Ustaw `RAG_BEDROCK_ENABLED=true` i zapewnij procesowi uprawnienie do wywołania
przypiętego modelu. Domyślnie wywołania Bedrock w serwerze są wyłączone.
Poświadczenia runtime używają standardowego łańcucha AWS; Compose nie montuje
automatycznie prywatnego profilu hosta. W produkcji to zakres roli runtime
i wdrożenia z późniejszych etapów.

Serwer ogranicza każdą przestrzeń do 1000 żądań i 5 MB na proces. Po wyczerpaniu
budżetu zwraca bezpieczny błąd usługi; restart rozpoczyna nowy budżet procesu.
To lokalna bramka kosztowa, nie rozliczenie wieloreplikowego środowiska.
Autoryzacja i walidacja kwalifikowanego pinu poprzedzają AWS. Filtry repozytoriów,
statusów, typów i dostępu oraz pilne blokady dokumentów działają w SQL przed
zwróceniem fragmentów, także dla starego pinu.

## Granica odbioru

Golden mierzy retrieval, cytaty i przypadki krytyczne. Nie mierzy jeszcze
groundedness wygenerowanej odpowiedzi ani wykonania narzędzi agenta: to AI 12.
Raport workera powstaje offline i ma koszt modelu `0`; koszt wcześniejszego
przygotowania w AWS jest osobnym pomiarem. Sam raport nadal ma
`activation_allowed=false`; dopiero qualified release uprawnia do aktywacji.
CI używa jawnych syntetycznych wektorów i rzeczywistego PostgreSQL, bez AWS.
