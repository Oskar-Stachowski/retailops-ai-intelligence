# Odbiór fake embeddings i kandydata pgvector — etap 11

Pomiar **2026-09-28**, macOS ARM64, Python 3.11.15, uv 0.12.19.
Implementacja: `fde43828a1030eb364faefba89bc60d3ce8488bd`.
[Raport JSON](11-index.json), [kontrakt i polecenia](../knowledge-index.md).
Zakres obejmuje offline część punktu 4 oraz zapis kandydata; etap 11 pozostaje
w realizacji. Nie obejmuje aktywacji ani semantycznego wyszukiwania.

## Rzeczywisty korpus

20 przypiętych dokumentów obu repo dało **302 fragmenty i 302 unikalne wektory**
fake, wymiar 32, L2 unit, cosine, float32. Artifact zawiera zamknięty graf
korpusu, fragmentów, metadata/cytatów, konfiguracji i embedding records.
Korpus i chunk manifest IDs pozostają takie same jak w poprzednim odbiorze;
konfiguracja embeddings i index ID są w raporcie.

Trzy builds — bieżące repo, niezależne kopie źródeł oraz czysty checkout kodu
z nowym venv — dały identyczne bajty. CLI z czystego venv uruchomiono spoza repo
z `PYTHONHASHSEED=173` i błędnymi settings serwisu; offline build nie korzysta z nich.
Artifact ma **1462828 bajtów**, uprawnienia `0600`, SHA-256:
`32473ee8d0a071ca1d81af6127a0a5d03fb703d0745bcff7a39d62e075a1ab88`.
Przechodzi także niezależną walidację JSON Schema.

Pełny kandydat zapisano przez kontroler i CLI przez stdin do rzeczywistej bazy
`retailops_ai` jako `ai_app`. Pierwszy zapis zwrócił `stored`, drugi
`already_present`, z tym samym index ID i licznikami 302/302. Wektory odczytane
binarnie z pgvector odtwarzają dokładne float32 i pierwotne checksumy.
Lokalny artefakt jest ignorowany przez Git; baza pozostaje na trwałym wolumenie.

## Testy i Required CI

44 nowe przypadki przechodzą w pełnym zestawie **433 testów**:
deterministyczność dla wymiarów 8/16/32/64, rozdzielenie przestrzeni, wspólny cache
identycznej treści z odrębnymi cytatami, zmiana metadata, usunięta treść,
błędny provider/config/wymiar/norma/float32, uszkodzony cache, tampering całego
grafu, bounded input, duplicate JSON keys, NaN, brak nadpisania artefaktu,
granice środowiska/bazy/roli oraz bezpieczne błędy CLI i zamknięcie engine.

`make bootstrap UV=.tools/bin/uv ci-local` — exit 0, **433 passed in 48.35s**.
Czysty checkout commitu implementacji, nowe venv i
`make bootstrap UV=.tools/bin/uv ci-local ... compose-smoke` — exit 0,
**433 passed in 57.73s**, bez pominięć. Ruff/format, Mypy strict (62 pliki),
docs, wszystkie snapshot checks, wheel/sdist, Compose config i oba Gitleaks
przechodzą. Actionlint przechodzi w obu checkoutach. Osłabiony index manifest
schema w osobnej kopii daje exit 1. Czysty checkout po odbiorze nie ma zmian Git.
Nie dodano zależności runtime; użyto istniejącego SQLAlchemy/Psycopg i pgvector.

Pełny smoke w czystym checkoutcie użył świeżego, izolowanego wolumenu PostgreSQL,
migracji **0002_rag_candidates** i pgvector **0.8.6**. Próby na małych Git fixtures:

- Natywny round trip float32/checksum, ponowny zapis bez nowych wierszy,
  rzeczywisty CLI przez stdin i bezpieczny błąd walidacji.
- Cache po zmianie access metadata, osobne przestrzenie wymiarów 32/8;
  poprawny strukturalnie, ale inny wektor pod istniejącym cache ID jest odrzucany.
- SQL odrzuca zły wymiar, NaN, zerową normę i niezgodną checksumę.
  Wszystkie cztery tabele odrzucają UPDATE oraz DELETE.
- Commit niekompletnego indeksu jest odrzucany. Próba połączenia różnych
  przestrzeni i wyjątek po zapisie fragmentów wycofują całą transakcję;
  liczniki space/cache/index/chunks nie zmieniają się po awarii.
- Dwa niezależne połączenia zapisujące jednocześnie jednego kandydata dają
  jeden nowy indeks i jeden replay. Nowy indeks nie zawiera usuniętej treści;
  stary kandydat pozostaje identyczny.
- SIGKILL/restart i down/up zachowują liczbę fragmentów syntetycznego indeksu.
  Pełny graf i bajty wektorów są sprawdzane przed restartem. Smoke sprawdza
  również bazę/artefakt MLflow, awarię DB/readiness, izolację ról, porty loopback
  i brak poświadczeń w logach. Kontenery zatrzymano; wolumeny pozostają.

Job `persistence` Required CI wywołuje ten rozszerzony `compose-smoke` i jest
wymagany przez `required-result`. To lokalny odbiór kodu; **nie wykonywano push
ani zdalnego Required CI dla tych commitów**.

## Granice

Fake vectors nie mierzą podobieństwa znaczeniowego. Nie ma oceny jakości,
golden set, near duplicates, ANN index, retrieval ani jego auth/status filters.
Korpus nadal ma `review_state=proposed`; nie ma zgody redakcyjnej, aktywnego
pointera, atomowej aktywacji ani rollback aktywacji. Nie wywoływano Bedrock/AWS.

Zapis wspiera tylko local/test i przypiętą przestrzeń fake. Hashe nie są
podpisem autora; operator i właściciel bazy pozostają granicą zaufania.
Stare kandydaty/cache zachowują treść historyczną; nowy indeks jej nie zawiera.
Garbage collection wymaga osobnego lifecycle. Następny zakres opisuje
[status](../STATUS.md): lifecycle indeksu, aktywacja i rollback po przeglądzie korpusu.
