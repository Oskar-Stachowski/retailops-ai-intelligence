# Odbiór testowego lifecycle indeksu — etap 11

**2026-09-28 · lokalny odbiór mechanizmu punktu 5; rzeczywisty korpus nieaktywny.**
Implementacja: `da01ece5e443c5a3e4b9ec5f58d7b01c33106eae`,
branch `ai/rag-corpus`, baza `e1f360f`.
[Wersjonowany pomiar JSON](11-lifecycle.json),
[procedura i granice](../knowledge-lifecycle.md), [bieżący status](../STATUS.md).

## Wynik

Jawna zgoda na korpus i deterministyczna walidacja są osobnymi, związanymi
artefaktami. Kwalifikacja wymaga kompletnego kandydata, właściwego corpus/config/
owner/environment oraz odtworzonego raportu `passed`. Nie tworzy zgody z samego
wyniku kontroli technicznej. Domyślny kanał `retrieval` odmawia kwalifikacji
i aktywacji bez rzeczywistego golden evaluation.

Migracja `0003_rag_lifecycle` dodaje niezmienne zgody, kwalifikacje i historię
oraz atomowy wskaźnik. Aktywacja/rollback wymagają właściwej generacji;
ponowienie identycznego request ID zwraca jego historyczny wynik. Odczyt jednym
statement tworzy pin pełnego manifestu. CLI nie dodaje narzędzia promocji dla agenta.

## Weryfikacja

- **463 testy** przechodzą w worktree i czystym checkoutcie commitu, odpowiednio
  52,49 s i 62,09 s. Nowe 30 przypadków obejmuje identity, binding zgody,
  deterministyczne raporty, fałszywe deklaracje jakości, strict inputs,
  bramkę golden/test environment oraz atomowy i bezpieczny CLI offline.
- Czysty checkout ma świeży venv z `uv.lock`. Przechodzą Ruff, format, strict
  Mypy (68 plików), dokumentacja, trzy bundles snapshots, wheel/sdist,
  Compose config, Gitleaks historii i katalogu. Actionlint przechodzi w obu
  checkoutach. Nie dodano zależności runtime.
- Rzeczywisty PostgreSQL 16/pgvector 0.8.6 sprawdza kwalifikację i jej replay,
  odmowę użycia niekwalifikowanego indeksu, złej generacji i rollback do wersji
  nigdy wcześniej nieaktywnej. Kanał retrieval oraz testowa aktywacja w `local`
  są odrzucane.
- Wyjątek wstrzyknięty **po zapisie wskaźnika** wycofuje wskaźnik i zdarzenie.
  Osobna sesja czytająca widzi stary pin aż do commit; po swapie zachowany pin
  i stary kandydat nadal odpowiadają pierwotnemu manifestowi. Retry wcześniejszej
  operacji zwraca pierwotny receipt i nie cofa bieżącego wskaźnika.
- Dwa niezależne promotery oczekujące tej samej generacji mają dokładnie jeden
  wynik `applied` i jeden konflikt. Rollback odtwarza wcześniejszy manifest pod
  nową generacją. Bezpośredni SQL nie może usunąć wskaźnika/historii, przeskoczyć
  generacji ani zatwierdzić samego zdarzenia bez zmiany wskaźnika.
- Realny CLI kwalifikuje, wykonuje rollback i odczytuje zgodny pin; domyślny
  kanał odmawia aktywacji. Pełny pin, obejmujący manifest i IDs zgody/raportu,
  pozostaje identyczny po SIGKILL/restart oraz down/up. Dotychczasowe kontrole
  persistence, DB outage/recovery, izolacji i logów nadal przechodzą.
- Worktree sprawdza istniejącą bazę, a świeży checkout pierwszą aktywację
  od generacji 0 na nowym wolumenie. Stosy użyte do odbioru są zatrzymane;
  wolumeny pozostają. Próby lifecycle używają wyłącznie syntetycznych fixtures.

Job `persistence` Required CI wykonuje rozszerzony `compose-smoke`. Jest to
lokalny odbiór: dla tych commitów nie wykonano push ani zdalnego Required CI.

## Rzeczywisty kandydat i granice

Korpus 20 dokumentów/302 fragmentów zachowuje dotychczasowe index ID i checksumę
artefaktu. `index-validate` utworzył raport mechaniczny `passed`, jawnie oznaczony
`not_evaluated_fake_vectors`. Czysty, zainstalowany CLI odtworzył raport identyczny
bajt po bajcie poza katalogiem repo, z innym hash seed i niepoprawnymi settings
serwisu, których ta komenda offline nie potrzebuje. Zapis ma `0600`.
Niezależny JSON Schema validator sprawdził ten raport oraz testowy pin.

Nie utworzono zgody redakcyjnej dla rzeczywistego korpusu, nie kwalifikowano
go do retrieval i nie aktywowano. Przypięte źródła nadal odnoszą się do wcześniejszych
rewizji obu repo; potrzebują przeglądu i odświeżenia przed użytkową aktywacją.
Fake vectors nie mierzą jakości semantycznej. Brakuje retrieval, golden set,
filtrów principal/access/status, near duplicates oraz agenta korzystającego z pin.

Hash i pola reviewer/actor nie są podpisem lub uwierzytelnieniem autora.
Artefakty dostarcza zaufany lokalny operator/pipeline z prywatnym dostępem DB.
Manifesty stworzone według storage revision `0002_rag_candidates` pozostają
niezmienne po migracji DB do `0003_rag_lifecycle`. Nie wykonywano AWS/Bedrock.

Następny zakres: ograniczony retrieval, golden set z zamrożonymi progami,
filtry dostępu/statusów oraz odbiór korpusu przed użytkową kwalifikacją.
