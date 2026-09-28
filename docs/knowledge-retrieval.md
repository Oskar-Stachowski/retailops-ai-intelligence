# Retrieval, uprawnienia i golden set — etap 11

Wyszukiwanie ma adapter exact cosine pgvector, deterministyczny ranking,
dywersyfikację źródeł i limity kontekstu. [Korpus](knowledge-corpus.md) nadal jest
propozycją. HTTP używa kwalifikowanego, przypiętego indeksu: w `test` tylko kanału
`offline_test`; w `local` kanału `retrieval`, który pozostaje nieaktywny.
Nie ma odpowiedzi LLM ani użytkowej aktywacji rzeczywistego korpusu.
[Dowody odbioru](evidence/11-retrieval.md) obejmują czysty checkout i realny PG/HTTP.

## Odczyt i dostęp

`POST /api/v1/knowledge/search` wymaga zweryfikowanego Bearer tokenu oraz jawnego
`knowledge:read` z `knowledge_scope`. [Lokalna polityka](access-control.md)
przypisuje środowisko, repozytoria, klasy dostępu i statusy. Brak capability lub
żądanie choć jednej wartości poza grantem daje 403. Sam admin nie dziedziczy odczytu.
Pominięte filtry oznaczają ograniczony default tego principal; nie dają dostępu
do obu repo lub `restricted`, jeśli nie przyznano ich jawnie.

Przykładowe body bez principal, roli, ścieżki pliku lub wyboru index ID:

```json
{
  "schema_version": "1.0",
  "question": "Jakie dowody pokazują lokalny runtime persistence?",
  "purpose": "verified_state",
  "filters": {"document_types": ["evidence"]},
  "top_k": 5,
  "max_context_tokens": 6000
}
```

| Purpose | Dozwolony stan źródeł w ramach grantu |
|---|---|
| `documentation` | Domyślnie specified/implemented/verified; historyczne tylko przez jawny filtr |
| `implementation` | implemented/verified, z zachowanymi code/evidence refs i fact scope |
| `verified_state` | verified, z przypiętym pomiarem, commitem i datą |
| `history` | historical/deprecated |

Filtry obejmują repozytorium, typ, status i klasę dostępu. SQL stosuje je oraz
aktualne blokady dokumentów **przed rankingiem i odczytem wyników**. Wszystkie
zapytania ograniczają index/environment/space/dimension. Wektor pytania ma
ten sam provider/config/wymiar co przypięty manifest; NaN, Infinity, zły wymiar
lub norma kończą się błędem przed wyszukiwaniem.

401 oznacza brak ważnego poświadczenia, 403 niedozwolony zakres, 422 niepoprawne
body. Brak kwalifikowanego indeksu/DB lub błąd adaptera daje bezpieczne 503.
Poprawne wyszukiwanie bez kwalifikujących się fragmentów albo miejsca w budżecie
zwraca `insufficient_evidence` i pusty kontekst. Nie klasyfikuje semantycznie
odpowiedzialności na pytanie; unrelated hit z fake nie dowodzi answerability.

## Ranking, cytaty i granice kontekstu

[Konfiguracja pakietu](../src/retailops_ai/knowledge/retrieval.default.json)
przypina `pgvector-cosine-exact-v1` i config ID. Brak HNSW/FTS/rerankera.
Cosine descending oraz chunk ID ascending rozstrzygają ranking i remisy.
Do dalszej selekcji wchodzą maksymalnie dwa najlepsze fragmenty dokumentu,
łącznie do 256. Przy limicie korpusu 128 dokumentów obejmuje to cały taki zbiór.
Selekcja rezerwuje najlepszy fragment każdego repo, następnie każdego dokumentu;
pozostałe wyniki zachowują ranking. Próg cosine wynosi 0.0.

Maksimum to pięć fragmentów i 24 000 bajtów serializowanego kontekstu `items`.
Estymacja `ceil(UTF8 bytes / 4)` obejmuje tekst oraz metadane/cytaty; budżet do
6000 tokenów można zaostrzyć w żądaniu. Jest estymacją, nie tokenizerem modelu.
Fragment, który nie mieści się w pozostałym budżecie, jest pomijany w całości.
Statement timeout wyszukiwania wynosi 5 s, connect timeout 3 s, pool wait 3 s;
nie deklarujemy 5 s jako deadline całego wielostatementowego requestu.

Każdy wynik zachowuje pełny chunk: repo, źródłowy SHA/path, heading path,
checksumy, status, access, fact scope, implementację/weryfikację i zakresy cytatów.
`claim_kind` odróżnia plan, implementację, pomiar i historię. Wynik ma
`content_trust=untrusted_reference`, `answer_generation=not_implemented` i jawne
oznaczenie fake. Nie scala konfliktu w jeden fakt, nie generuje odpowiedzi ani
nie wywołuje narzędzia na podstawie tekstu dokumentu.

Resolver odczytuje jeden [pin](knowledge-lifecycle.md) przed wyszukiwaniem.
Adapter `search_pinned` może ponownie używać tej wersji podczas zmiany current;
bieżące blokady nadal obowiązują. Serwis zwraca `Cache-Control: no-store`.
Nie ma współdzielonego cache odpowiedzi/query vectors; każdy odczyt ponownie
sprawdza grant i SQL deny. Polityka poświadczeń nadal wymaga restartu po zmianie.
Dotychczasowe manifesty zachowują creation/storage revision 0002 oraz pierwotne
`retrieval_version=not_implemented`. Wynik i ewaluacja osobno wiążą nowy config
retrieval; użytkowy release manifest/polityka wymagają kolejnego odbioru.

## Pilne odebranie dostępu do dokumentu

Migracja `0004_rag_denials` dodaje niezmienną listę blokad według environment
i document ID, obejmującą wszystkie rewizje tej tożsamości, również stare piny.
Kontrolowany operator z prywatnym dostępem DB może przekazać `DocumentDenial`
do `knowledge-deny --denial FILE [--env-file PATH]`. Pole environment musi
odpowiadać settings; artefakt ma actor i bezpieczny kod reason. Identyczne
ponowienie jest idempotentne; inna treść pod tym samym kluczem daje błąd.
Nie ma endpointu agentowego ani automatycznego przywracania dostępu.

Blokada zatwierdzona przed statement wyszukiwania obowiązuje od jego snapshotu.
Nie odwołuje danych już zwróconych w poprzedniej odpowiedzi. Hash dokumentu
nie zastępuje grantu; posiadanie document/index ID nie uprawnia do odczytu.

## Golden set i ewaluacja

[Golden v1](../knowledge/golden.v1.json) ma **30–50 wersjonowanych pytań**: dokumentacja, modele,
operacje, brak danych, konflikt, injection i autoryzacja. Ręcznie napisane etykiety
wskazują repo/path/heading/status, forbidden sources, answerability, role/scope
oraz required/forbidden tools dla etapu 12. Zbiór i korpus mają `review_state=proposed`;
nie przypisujemy im akceptacji właściciela. Nie generujemy etykiet z rankingu.
Przed pierwszą ewaluacją zamrożono progi: Recall@5 ≥0.8, MRR ≥0.6, poprawność
bindingu cytatów i krytyczne kontrole 100%, groundedness ≥0.95, p95 ≤1000 ms,
koszt wywołań modelu offline 0 USD. IDs wiążą etykiety, progi, indeks i config.

```bash
.tools/bin/uv run --locked retailops-ai knowledge-evaluate \
  --candidate .local/rag/index-candidate.json \
  --golden-set knowledge/golden.v1.json \
  --output .local/rag/golden-report.json
```

CLI jest prywatną ewaluacją kandydata offline, bez settings, DB lub aktywacji.
Tworzy nowy plik `0600` i małe podsumowanie, nie nadpisuje raportu. Exit 0 oznacza
wykonanie ewaluacji; bramki odczytaj z raportu. Błędny artefakt ma exit 2.
Kontrola etykiet odrzuca nieobecne sekcje oczekiwane i zabronione, duplikaty
etykiet i mismatch index/config. Sekcja zabroniona musi istnieć w pełnym
kandydacie, nawet jeśli jej status lub access uniemożliwia zwrócenie jej principal.
Zmiana/usunięcie źródła wymaga nowej etykiety przed ewaluacją.

Raport zawiera Recall@5/MRR, brak forbidden sources, binding cytatów, odmowy,
latency i koszt offline. Binding cytatu mierzy zgodność z przypiętym chunkem,
nie prawdziwość twierdzenia. Konflikt ma wiele oczekiwanych źródeł; synteza
odpowiedzi/rozstrzygnięcie pozostają pracą agenta. Groundedness jest null, a
narzędzia mają etykiety bez wykonanej ewaluacji agenta. `activation_allowed=false`
obowiązuje także przy idealnym wyniku fake. Naturalne pytania z fake mają niską
jakość; raport nie stanowi odbioru jakości semantycznej ani release approval.

[Osobny adversarial fixture](../tests/fixtures/rag/adversarial.v1.json) jest używany
tylko w testach; nie trafia do zwykłego rejestru. Próby PG/HTTP odbierają prompt
injection jako niezaufaną treść, uprawnienia, scope, deny po wcześniejszym odczycie
oraz pin podczas swapu. CI nie korzysta z AWS.

Podstawa: [pgvector 0.8.6 — exact search i cosine](https://github.com/pgvector/pgvector/tree/v0.8.6),
[PostgreSQL 16 — window functions](https://www.postgresql.org/docs/16/functions-window.html).

## Następny odbiór

[Administracyjne runy](knowledge-administration.md) i
[kontrola podobnych treści](knowledge-review.md) mają osobne odbiory testowe.
Do zamknięcia etapu 11 pozostają akceptacja źródeł/etykiet oraz odbiór jakości
i użytkowej kwalifikacji. Każda zmiana źródeł wymaga ponownego przeglądu.
Real embeddings/Bedrock smoke i agent należą do etapu 12.
