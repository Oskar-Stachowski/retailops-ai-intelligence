# Odbiór kontroli kwalifikacji — etap 11

**2026-09-28 · zakres offline/fake, bez aktywacji.**
[Pomiar JSON](11-qualification.json), [instrukcja](../knowledge-qualification.md),
[aktualny status](../STATUS.md).

`index-release-check` zapisuje deterministyczny manifest wiążący corpus/index,
konfiguracje, golden set, pełny raport, walidację mechaniczną i decyzje przeglądu.
Raport golden jest odtwarzany na poziomie wszystkich wyników/cytatów i metryk;
kontrola nie ufa samemu podsumowaniu. Oryginalne timing/p95 pozostają przypiętym
pomiarem z kontrolą spójności. Nie deklarujemy nowej atestacji czasu.

33 testy przechodzą (13,20 s), w tym podmienione metryki/wyniki/cytaty,
brak/przestawienie pytań, niezgodne p95/progi, poprawne strukturalnie lecz złe
fake vectors, ponowne użycie zgód dla innych pinów/owner/environment,
nieznane/stare/zdublowane decyzje podobieństwa oraz częściowy przegląd.
Idealny fake report i obie jawne zgody nadal dają `blocked`.
CLI test potwierdza brak settings, tryb `0600`, brak nadpisania oraz bezpieczny
błąd bez nowego artefaktu. Strict Mypy obejmuje 89 plików źródłowych.

## Wynik dla przypiętego korpusu

Kandydat nadal ma 29 dokumentów/451 fragmentów, AI `082bed4` i RetailOps
`78f801f`, 44 pytania i 9/9 przypadków krytycznych. Rejestr, etykiety,
chunker/embedding/similarity policy oraz prywatny indeks pozostają niezmienione.

Jedna dotychczasowa techniczna decyzja o krótkim wspólnym wprowadzeniu ma teraz
wersjonowany, typowany artefakt związany z report ID. Przegląd pokrywa 1/1
finding, nie tworzy zgody na korpus. Walidacja mechaniczna jest `passed`;
golden Recall@5/MRR nadal nie przechodzą. Rzeczywiste zgody pozostają null.

Manifest ma ID
`rag-release-manifest-sha256-1dab065f38efb964f2d74b913233f5eb4488ae02486fd511caef98a352295dc0`
oraz pięć blokad:

- `corpus_approval_missing`,
- `golden_labels_approval_missing`,
- `golden_thresholds_failed`,
- `semantic_provider_required`,
- `user_build_profile_required`.

Prywatny manifest nie zawiera tekstów chunków/wektorów i ma `0600`.
Nie zmieniono schematu DB, testowej kwalifikacji/wskaźnika, HTTP, runów ani
drugiego repozytorium. Nie uruchomiono AWS i nie wykonano push.
Pozostają jawne decyzje właściciela, użytkowy profil/kwalifikacja oraz real
provider/golden quality w etapie 12.
