# AI 08 — końcowe READY

**2026-10-05: cały etap AI 08 jest READY.** [Trwały receipt](08-28-final-ready.json)
wiąże przyjęte rewizje obu repozytoriów, rzeczywiste wyniki kontroli i granice odbioru.
Warunki formalne zostały spełnione: PR #14 ma zielone wymagane CI, został scalony
normalnym chronionym trybem, a jego merge commit ma zielone CI na main.

| Repozytorium | Przyjęty main kodu | Required CI na tym main |
|---|---|---|
| RetailOps AI Intelligence | `2565a216fa7807a756387dcbaa408d6ccc07840d` | [success](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37288700026) |
| RetailOps | `684f18f999ce6bddc5aa83bf6d7fa7aaef9fc832` | [success](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37262096351) |

[PR #14](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/14)
i [jego Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37282434148)
są zapisane bez zastępowania odbioru PR ręcznym workflow. Pełny zestaw kodu ma
**2543 zaliczone testy**, Ruff, Mypy, kontrakty/pakiet, skan całej historii i
rzeczywiste testy SQL/MLflow oraz backup/restore. Receipt zachowuje wyniki każdego
jobu; istniejące pominięcia niewybranych obszarów RetailOps nie są nowymi testami.

## Zamknięty zakres

- [Niezależna jakość](08-25-final-campaign-results.md): sześć źródeł, 9296 punktów,
  **90 kontroli zaliczonych, 3 zaakceptowane ostrzeżenia i 0 blokad**.
- [Kwalifikacja i karta](08-26-qualified-model.md): zamrożony LR with_upstream,
  conditional sigmoid C10, progi 25/50/90% oraz capacity top50% wybrane na development.
- [Rzeczywisty odbiór modelu](08-27-final-serving-acceptance.md): 206 testów,
  12 bramek review, PostgreSQL i MLflow, register/promote/rollback/reject,
  cold worker, 40 wyników batch, 15 pozycji attention i uwierzytelnione scoped API.
- Dojrzałe etykiety, fizyczne lokalizacje, PIT, historyczny upstream, brak future
  delivery/truth w cechach, temporal purge, LR/HGB i porównanie censored sales
  pozostają udokumentowane w wcześniejszych dowodach i karcie.

Trzy ostrzeżenia dotyczą małych kategorii według reguły zatwierdzonej przed final
oceną. Pierwotne wyniki strict calibration pozostają widoczne. Nie zmieniono
kryteriów po obejrzeniu final outcomes i nie dopasowano ponownie modelu.
Dawne nieudane próby oraz wpisy not ready są historią przyrostów, nie bieżącym statusem.

## Granice i wznowienie

Nie ma pozostałych wymaganych prac AI 08. Dane są syntetyczne; sześciu światów
ani nakładających się okien nie należy łączyć jako niezależnych epizodów.
Koszty FP/FN są jednostkami ilustracyjnymi. Odbiór odbył się na odizolowanych
usługach, bez wdrożenia produkcyjnego. Historyczne READY nie odnawia bieżącego
origin, freshness ani ważności approval do nowej promocji.

Kolejna sesja korzysta z tego raportu i karty modelu, zamiast ponownie otwierać
zamkniętą kampanię. AI 07 ma osobny odbiór; AI 09/10 zachowują własne zależności.
Publikacja tego zapisu jest dokumentacyjna i ma własne wymagane CI.
