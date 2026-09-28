# Odbiór parsera i chunkera — etap 11, punkt 3

Pomiar **2026-09-28**, macOS ARM64, Python 3.11.15, uv 0.12.19.
Implementacja: `96a47a3509e20cb464117a162e56b7f69a6e5d5b`;
baza: `be748a31edaec2822de361ea1bb6d83b1950d4ce`.
[Raport JSON](11-chunks.json), [polecenia i kontrakt](../knowledge-chunks.md).

## Rzeczywisty korpus i powtórzenie

Źródła pozostają przypięte do odebranych main SHA:
AI `7940d2ee7c82dee4e99d49755fc7eb898f15b2ca`, RetailOps
`b8de65b53b3f27863bb11999ca6e9a5d6d41a760`. Wybrane 20 dokumentów
dało **302 fragmenty**: 219 akapitów, 26 tabel, 26 bloków kodu, 20 list
nieuporządkowanych, 7 uporządkowanych i 4 cytaty. Maksimum: 2030 bajtów UTF-8,
estymacja 508 tokenów przy limicie konfiguracji 2048 bajtów.

W rzeczywistym wyborze pominięto 99 nagłówków jako strukturę; ich ścieżki są
na fragmentach. Pozostałe reguły pomijania oraz scalanie duplikatów sprawdzono
w Git fixtures; ten korpus nie dał takich pominięć ani dodatkowych wystąpień.
Żaden wybrany dokument nie był bez treści.

Każde wystąpienie porównano z dokładnym wycinkiem źródła oraz niezależnie
przeliczonymi numerami linii. Fragmenty i jawne pominięcia pokrywają całą treść
poza białymi znakami. Niezależna walidacja JSON Schema przechodzi również
na rzeczywistym manifeście.

Oryginalny checkout, odrębne kopie obu Git repo bez hardlinks, czysty checkout
implementacji z nowym venv i CLI uruchomiony spoza checkoutu dały **identyczne
bajty** manifestu. SHA-256:
`a28505ab65e6f209e0cbd2b72260a7b284e96c810cb4068290f7bba5c975e7cf`.
Plik ma 907651 bajtów i uprawnienia `0600`; pozostaje lokalnym artefaktem poza Git.
Logiczne corpus/chunker/chunk manifest IDs i przykładowy cytat są w raporcie.

## Testy i kontrole

48 nowych przypadków chunkera przechodzi w pełnym zestawie 389 testów.

- Nagłówki ATX/setext, inline formatting, skoki poziomów, preamble;
  nagłówki w kodzie/cytacie nie tworzą sekcji. Listy, tabele, HTML i definicje
  linków zachowują tekst źródłowy bez wykonywania lub fetchowania.
- Nawigacja, komentarze, jawny dokument bez treści, dokładne duplikaty z wieloma
  zakresami cytatów. Ten sam tekst w innych sekcjach lub dokumentach zachowuje
  odrębne IDs i source/access metadata.
- Duże akapity, listy, kod, tabele i pojedyncze linie; polski, CJK, emoji
  i znaki łączące. Limity UTF-8, bez utraty istotnej treści i dzielenia kodowania znaku.
- Zmiana commitu, przesunięcie sekcji/ordinalu, dirty worktree, kolejność rejestru,
  LF/CRLF oraz odrębne klony. Niezmieniony blok zachowuje chunk ID; cytat i parent
  document hash aktualizują się do rewizji kandydata.
- Edycja lub usunięcie bloku/dokumentu nie zostawia starych IDs w nowym grafie.
  Usunięcie nadal zarejestrowanego pliku przerywa build; jawna aktualizacja
  rejestru usuwa dokument i jego fragmenty z nowego manifestu.
- Zmiana kontekstu sekcji/config oraz access/environment; metadata źródła,
  tożsamości, checksum, ordinal, cytaty, zakresy, overlap i kompletność grafu.
  Osierocony/brakujący fragment, nieznany algorytm lub drift wersji parsera
  są odrzucane. Limit liczby fragmentów zatrzymuje build.
- CLI działa mimo błędnego APP_ENV, wypisuje tylko podsumowanie, tworzy nowy
  plik atomowo i nie nadpisuje istniejącego. Brak pliku źródła nie tworzy
  częściowego wyjścia; błędy nie zawierają lokalnych ścieżek ani traceback.

`make bootstrap UV=.tools/bin/uv ci-local` — exit 0, **389 passed in 36.43s**.
Czysty checkout commitu implementacji i nowe venv: ten sam zestaw bramek —
exit 0, **389 passed in 44.01s**, bez pominięć. Ruff/format, Mypy strict (56 plików),
docs, snapshot checks, wheel/sdist, Compose config i oba Gitleaks przechodzą.
Actionlint przechodzi w obu checkoutach. Celowo osłabiony schema chunków
w osobnej kopii daje exit 1 gate. Czysty checkout po próbach pozostaje bez zmian Git.

Runtime dodaje markdown-it-py 4.2.0 i transitive mdurl 0.1.2, przypięte z hashami
w uv.lock. Oba pakiety mają MIT, sprawdzone w classifier i dołączonych license files.
Checksum zbudowanego wheel jest w raporcie; nie jest to osobny test instalacji
wheel do środowiska wyłącznie produkcyjnych zależności.

## Ograniczenia

Punkt 3 jest odebrany jako parser i kandydackie manifesty. Etap 11 pozostaje
w realizacji. Pomiar tego commitu nie obejmował embeddings, indeksu pgvector,
retrieval, golden set, aktywacji ani rollback. Access/status
są odziedziczonymi metadata, bez runtime auth/cache. Near duplicates nie są
automatycznie usuwane. Token estimate nie dowodzi limitów docelowego modelu.
Duże bloki mogą być podzielone na niezamknięte składniowo wycinki Markdown.

Nie zmieniono migracji DB; nie powtarzano pełnego Compose persistence smoke.
Odbiór był lokalny, bez zdalnego Required CI dla tego zakresu.
[Fake embeddings i persistence](../knowledge-index.md) mają własny późniejszy
odbiór. Aktualną następną pracę opisuje [status](../STATUS.md).
