# Asystent pytań o dokumentację

Ten dokument zachowuje historyczny profil `document-runtime.v1` i jego
[odbiór](evidence/12-document-runtime.md). Profil jest przypięty do wcześniejszego
kodu; nie przechodzi ładowania w bieżącej wersji aplikacji. Opisane niżej kwoty,
trasy i migracja `0010` dotyczą tamtej próby. Bieżący serwis wymaga migracji
`0028_ai12_suggestion_outbox`. Nowe przygotowanie używa [runtime natywnego](assistant-native-bedrock.md)
i [punktu wznowienia przed płatną kwalifikacją](ai12-paid-qualification.md);
nie należy przenosić historycznego wyniku na nową konfigurację.

## Obsługiwane pytania

[Profil](../agent/document-runtime.v1.json) obsługuje dokładnie:

- „Które endpointy zwracają zweryfikowaną tożsamość i scope?”
- „Kiedy zmiana grantów lub revoke wymaga restartu procesu?”

Normalizacja obejmuje wielkość liter, odstępy i Unicode NFC. Parafrazy i inne
pytania zwracają 422 przed admission i AWS. Rozpoznane żądanie wykonania
operacji, np. „Place an order”, otrzymuje odmowę bez modelu.
To początek plannera; ogólny język naturalny i intencje biznesowe wymagają
osobnego rozszerzenia oraz odbioru.

Każde pytanie ma wymagania i pełne hashe konkretnych fragmentów źródłowych.
Retrieval nadal wykonuje zwykłe wyszukiwanie top-5 w kwalifikowanym indeksie,
z filtrami uprawnień, statusów i bieżącymi wykluczeniami dokumentów. Dopiero
pobrane fragmenty mogą pokryć wymagania; wpis w konfiguracji nie zastępuje
wyszukiwania. Serwer porównuje pełny fragment oraz literalny cytat.

Do modelu trafiają sprawdzone fakty, odpowiadające im cytowania i dozwolone
wywołania. Pełne wyniki RAG pozostają po stronie serwera do walidacji. Ta sama
projekcja jest używana przy CountTokens i Converse. Ogranicza to rozmiar
kontekstu i zostawia miejsce na jedną naprawę, bez zmiany progów dowodowych.
Odpowiedź nadal zawiera kanoniczne twierdzenia i cytaty; nie jest swobodnym
streszczeniem. Status `implemented` oraz rewizja źródła pozostają widoczne.

## Identyfikatory i dostęp

Resolver ładuje zweryfikowany import [AI 03](source-snapshot-import.md):
`product_catalog`, `selling_locations` i `channel_assignments`. Sprawdza
niezmienność plików, oryginalne UUID, dostępność produktów i przypisanie
lokalizacji do serwerowego kanału przez każdy dzień okresu. Luki,
niejednoznaczne przypisania, obce ID i przyszły okres są odrzucane.
`store_ids` oznacza tutaj source `selling_location_id`, bez aliasów legacy.

Profil wiąże source/snapshot IDs, hash katalogu, cały pin RAG, kod pakietu,
graf, model, prompty i limity. Zmiana aktywnego pinu daje 424; runtime nie
przełącza się automatycznie na nowy indeks. Zmiana kodu/źródła wymaga nowego
przejrzanego profilu i powtórzenia właściwych testów.

Grant wymaga roli `operator`, `assistant:query`, jawnego zakresu produktów,
lokalizacji i kanałów oraz `knowledge:read` z `knowledge_scope`. Prawa do
sprzedaży czy forecastu nie są potrzebne do pytań o dokumentację. Zapisany
trace jest dostępny właścicielowi z nadal zachowanymi prawami; cudzy daje 404.

## Uruchomienie lokalne

1. Przygotuj środowisko z dodatkiem `snapshot`: `make bootstrap`.
2. Przygotuj bazę AI i wykonaj jawną migrację:
   `retailops-ai migrate --env-file .local/assistant.env`.
   Wymagana rewizja to `0010_assistant_token_budget`.
3. W tej bazie musi istnieć kwalifikowany i aktywny pin wskazany w profilu.
   [Instrukcja RAG](knowledge-semantic.md) opisuje jego przygotowanie.
4. Zaimportuj source: `retailops-ai-snapshot import --snapshot-dir data/fixtures/ai-smoke-v1/snapshot`.
   Wskaż opublikowany katalog importu, nie katalog wejściowego fixture.
5. W prywatnym pliku konfiguracji ustaw poniższe opcje oraz DATABASE_URL
   i API_AUTH_FILE. Poświadczenia AWS pochodzą ze standardowego łańcucha SDK.

```dotenv
APP_ENV=local
ARTIFACT_ROOT=./artifacts
RAG_BEDROCK_ENABLED=true
ASSISTANT_RUNTIME_FILE=agent/document-runtime.v1.json
ASSISTANT_SOURCE_IMPORT=data/generated/snapshots/source-sha256-a12866e1099c3ae2ae7c73cac5c533a35733618d85a3728cd5f0e1c7b527fc00
```

`retailops-ai serve --env-file .local/assistant.env` składa planner, resolver,
PostgreSQL store, przypięty RAG i leniwy provider Bedrock. Start procesu nie
wywołuje AWS. Kontrola dostępu konta i profilu EU następuje dopiero po
zatwierdzeniu uprawnień, zakresu i trwałym admission pierwszego żądania.
Bez obu opcji ASSISTANT standardowe query nadal zwraca 503.

Profil źródła wykorzystuje zaakceptowany syntetyczny fixture AI 03:
20 produktów, 2 lokalizacje, 6 wersjonowanych przypisań. W odbiorze użyto
okresu 2026-07-05–2026-07-06. To nie jest odczyt bieżącej sprzedaży ani ML.
Korpus dokumentacji jest przypięty do starszej rewizji `082bed47…`; zmiana
`main` nie aktualizuje automatycznie odpowiedzi.

Downgrade do `0009_assistant` wymaga braku rekordów z rezerwacją większą
niż 13 500 tokenów. Migracja nie obniża zapisanych rezerwacji: poczekaj na
retencję i cleanup albo zarchiwizuj oraz usuń wyłącznie własne rekordy testowe.

## Budżet i powtórzenie testu

Profil ogranicza chat do 0,18 USD na proces, 0,11 USD na żądanie,
16 000 tokenów wejścia i 3000 wyjścia łącznie, 1500 wyjścia na wywołanie,
jednej naprawy i 45 sekund. Oddzielny provider embeddings ma wspólny limit
4 żądań / 4000 bajtów wejścia na proces. Retriable/nieudane próby także
zużywają limity. Rezerwacja SQL obejmuje maksymalnie 19 000 tokenów chatu.

Kwota `estimated_cost` w trace dotyczy chatu. Embeddings wymagają odrębnego
rozliczenia/rezerwy. W odbiorze zachowano konserwatywne 0,005 USD na próbę;
nie uznajemy tej rezerwy za dokładny rachunek AWS. Cena opisana przez
[AWS dla Titan V2](https://aws.amazon.com/blogs/machine-learning/get-started-with-amazon-titan-text-embeddings-v2-a-new-state-of-the-art-embeddings-model-on-amazon-bedrock/)
wynosi 0,02 USD/milion tokenów; rezerwa używa wyższego założenia 0,10 USD
oraz maksymalnych 8192 tokenów na każde z czterech wywołań.

Restart zeruje lokalne liczniki providera. Limity procesu ani ruchome okno
admission nie zastępują łącznego budżetu kampanii. Przed każdym płatnym
powtórzeniem sprawdź [rejestr kosztów](evidence/12-bedrock-budget.json)
i zarezerwuj pełny limit; nie zwiększaj zatwierdzonej kwoty bez zgody.

[Skrypt odbioru](../scripts/verify_document_runtime.py) wykonuje dwa pytania
przez skonfigurowaną aplikację HTTP, prawdziwy PostgreSQL, Titan i Converse.
Wymaga jawnego `--execute-aws`, prywatnych plików tokenów właściciela i innego
uprawnionego operatora oraz JSON query z dozwolonym zakresem. Tokeny nie są
argumentami procesu. Plik wyniku musi być nowy; skrypt zapisuje start przed
wywołaniami i nie nadpisuje wcześniejszego odbioru.

```sh
.venv/bin/python scripts/verify_document_runtime.py --execute-aws \
  --env-file .local/assistant.env \
  --token-file .local/owner.token --foreign-token-file .local/foreign.token \
  --query-file .local/query.json --golden agent/document-runtime.golden.v1.json \
  --output .local/document-runtime-receipt.json
```

Skrypt wymaga deweloperskich zależności testowych. Nie podmienia adapterów
runtime i nie aktualizuje etykiet na podstawie wyniku modelu.
