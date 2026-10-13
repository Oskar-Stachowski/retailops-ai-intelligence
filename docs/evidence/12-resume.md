# AI12 — wznowienie 2026-10-07

**Status: in_progress.** [Receipt](12-resume.json),
[pytania i ograniczenia](../assistant-routing.md).

`ai/12-resume` łączy zachowany `ai/12-tools` na `a14b7899` z `main` na
`3329a815`. Rozwiązano 15 konfliktów w API, uprawnieniach, kontraktach,
dokumentacji i lockfile. Role `pipeline`/`promoter`, natywne API prognoz,
anomalii i stockout oraz ich zakresy zostały zachowane. Fizycznego
`stockout_scope` nie zastąpiono zakresem punktów sprzedaży.

Head `0026_ai12` łączy opublikowane historie migracji `0023_ai07_ai08`
i `0010_assistant_token_budget`. Nie uruchomiono migracji na wspólnej bazie.
Rzeczywisty PostgreSQL/Compose należy do wymaganej zdalnej bramki persistence;
obecny `compose-smoke` obejmuje Assistant admission, atomowy zapis,
bezpieczny odczyt trace, restart i utrzymanie wyników.

Nowy profil proponowany zawiera 26 pytań / 12 intencji. Planer sprawdza scope,
kanał, uprawnienia do każdego narzędzia, dostępność adapterów oraz katalog Source
w punkcie odcięcia. Porównanie sprzedaży ma dwa rozłączne okresy tej samej
długości. Prognoza używa ostatniego zamkniętego dnia UTC, bez przyszłego
znacznika czasu. `reviewed_backend` wiąże te dane z code/config ID i rzeczywistym
zestawem adapterów; nie tworzy klienta AWS.

## Wyniki lokalne

- 687 unikalnych, wybranych testów ma końcowy wynik passed. Obejmują agenta,
  Assistant HTTP/store, routing, uprawnienia, API stockout, prognozy/modeli/v12,
  migracje, diagnostykę i kontrakt required CI. To zakres regresji tej zmiany,
  nie deklaracja uruchomienia wszystkich testów repozytorium.
- Pierwszy przebieg: 679/682 passed. Trzy błędy dotyczyły odwołań testów do
  historycznych smoke profiles. Po poprawieniu ścieżek 48/48 testów Bedrock
  przeszło; ostatni przebieg routing/evaluation/Bedrock dał 122/122 passed.
- Ewaluacja offline: 50/50 przypadków i 36/36 krytycznych, wszystkie bramki
  passed, zero zbędnych tool calls, koszt syntetyczny 0 USD. Golden labels
  pozostają `proposed`; source fixtures nie potwierdzają jakości runtime.
- Ruff, mypy (532 pliki źródłowe), wszystkie `contracts-check`, build sdist/wheel
  i kontrola sekretów passed. Code hash 532 plików Python w wheel jest identyczny
  z checkoutem; wheel zawiera nowy planer i migrację łączącą historie.
- Nowy release offline i profile `.resume.v1` wiążą aktualny kod oraz lockfile.
  Historyczne release, konfiguracja runtime i receipts Bedrock są zachowane.
  Nie wykonano nowych wywołań AWS. Przygotowana propozycja smoke ma
  `status=not_run` i wymaga jawnego limitu kosztu.

## Do dalszego odbioru

1. Rzeczywiste adaptery biznesowe/ML: natywne typy, jednostki, source/snapshot,
   model/release, świeżość i autoryzacja z AI10. Historyczny tool output v1
   nie może powstać przez niezweryfikowane przepisanie natywnego read projection.
2. Jawne powiązanie punktów sprzedaży z fizycznymi lokalizacjami stockout.
   Planer już wymaga fizycznego zakresu produktu; adapter musi sprawdzić
   także dostęp do właściwych miejsc składowania.
3. Kwalifikacja dziennej świeżości prognoz względem krótkiego czasu życia
   sugestii. Obecna polityka nie wyda aktualnej sugestii z wygasłych dowodów;
   nie poszerzono limitów, żeby sztucznie uzyskać rekomendację.
4. Sugestia rzeczywistego agenta/polityki → trwały outbox/v2 → RetailOps
   read API/UI, z tymi samymi IDs i jednym efektem po retry. Wymaga AI10.
5. Przegląd proponowanych pytań, etykiet i konfiguracji oraz bounded Sonnet/Titan
   na zaakceptowanym indeksie AI11 i uprawnionych rzeczywistych narzędziach.
   Nowy limit wydatku musi pokryć najgorszy koszt ustalonego zestawu.
6. Wszystkie wymagane bramki CI na opublikowanym head, w tym PostgreSQL/Compose.

Praca odbyła się w osobnym worktree i własnym `.venv`. Nie zmieniono katalogów
AI09/AI10, ich procesów, wspólnego Compose ani baz. Nie uruchamiano treningów,
eksportu Source ani generowania nowych pełnych danych.
