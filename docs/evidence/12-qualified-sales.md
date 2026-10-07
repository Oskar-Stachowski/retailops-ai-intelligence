# AI12 — sprzedaż z pełnych kwalifikowanych dni

2026-10-07, **in_progress**. [Receipt](12-qualified-sales.json) wiąże kod,
konfiguracje, lokalną regresję i zakres zdalnego CI. [Opis adaptera](../agent-qualified-sales.md).

`QualifiedSalesReader` odtwarza istniejący łańcuch Source → Curated → pełny Raw
DQ → zamknięcia dni przy starcie, a następnie odczytuje prywatną migawkę wiedzy
na żądany cutoff. `QualifiedSalesTool` wymaga dokładnej autoryzacji i pełnej
siatki dni. Jedna niekwalifikowana doba wstrzymuje cały okres; jawne zero
wymaga potwierdzenia wszystkich dni. Nie odejmuje zwrotów, nie wnioskuje
nieocenzurowanego popytu i nie utożsamia magazynu z lokalizacją sprzedaży.

W zaakceptowanym wyniku pozostają dzienne `Point`, statusy i identyfikatory
Source/Curated/replay/coverage/runtime. Walidator sprawdza czas, zakres,
jednoznaczność waluty, dokładność sum i ich zgodność z wynikiem. Executor
wymaga dowodu dla danych runtime. Planner sprawdza wszystkie serie i limit
200 punktów przed admission. Historyczne fixtures zachowują serializację bez
nowego pola; etykiety i checksum golden pozostają niezmienione.

Walidacja:

- 58/58 testów adaptera, w tym autoryzacja, limity, zmiany dziennych dowodów,
  brak deklaracji, niezamknięte/missing/closed dni, jawne zera i graf.
- Oba istniejące, oznaczone fixtures Source są rzeczywiście weryfikowane przez
  oryginalny importer, Curated i DQ. Odczyt zgadza się z niezależnie zsumowanymi
  przyjętymi faktami. Ponowne związanie readera zachowuje wynik.
- HTTP przechodzi planner, rzeczywisty reader, adapter, graf i testowy store
  dla znanej sprzedaży i closed day; claim ref i zapisany trace wskazują
  odpowiednią migawkę. Fake chat i testowy store są jawne.
- Szersza regresja: 652 unikalne przypadki z zaliczonym końcowym wynikiem.
  Pierwszy run miał 650 passed i dwa błędy importu pomocnika w nowych testach
  HTTP. Po poprawce ponowiono cały plik adaptera: 58/58 passed, bez zmiany
  aplikacji. Receipt zachowuje oba raporty i rozróżnia je.
- Pełne `ci-checks` passed: Ruff, mypy 656 plików, dokumentacja/runtime,
  wszystkie kontrakty, fake golden 50/50 i 36/36 critical, build i Compose config.
- Wheel i checkout mają identyczne 537 modułów Python i oba runtime lockfile'y.
  Odrębny proces z importem bezpośrednio z wheel przechodzi ten sam golden,
  z połączeniami sieciowymi zablokowanymi i bez namespace producenta.
- Plan CI: 4163 testy / 186 plików, cztery rozłączne shardy, wszystkie 58 sales
  i 41 native forecast cases dokładnie raz. Nie wykonano całej kolekcji lokalnie.
- Gitleaks drzewa i osiągalnej historii passed.

Kandydaty `.native-sources.v1` wiążą aktualny kod. Poprzednie manifesty,
receipts, etykiety i modele pozostają zachowane. Scalenie opublikowanego
`main/89b64d23` jest zawarte w `ad8540c` i nie zmieniło drzewa checkpointu
adaptera `ee187cf`, w tym pełnego checksumu aplikacji. Zachowano
`agent-evaluate`, frozen training lock oraz wszystkie bramki CI.

[Required CI poprzedniego head `35dae7e`](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37626054864)
zakończyło wszystkie **15/15** jobów sukcesem. Nowy adapter sprzedaży nie
dziedziczy tego odbioru: nowy publikowany head wymaga osobnego pełnego CI.

Nie wykonano AWS, nowych pełnych eksportów Source ani treningu. Nie zmieniono
sąsiednich worktree, procesów, baz lub wspólnego Compose. Ten przyrost nie
kwalifikuje produkcyjnego runtime ani trwałości transportu. Do READY nadal
pozostają inne adaptery biznesowe/ML i fizyczny mapping, natywne powiązania
risk/model dla sugestii, rzeczywisty przepływ AI10 outbox/v2 → API/UI,
przegląd pytań/konfiguracji i ograniczony budżetem odbiór Sonnet/Titan.
