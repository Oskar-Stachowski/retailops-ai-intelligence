# AI 04.2 — lokalny odbiór panelu i cech

Data: 2026-09-29. Repo: `retailops-ai-intelligence`.
Branch: `ai/04-01-task-calendar`, kontynuacja lokalnego commita 04.1 `124a4ea`.
Zakres odpowiada punktowi 04.2
[planu](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/etapy/04-forecasting.md).
[Runbook](../forecast-features.md) opisuje znaczenie danych i polecenia.

## Wynik funkcjonalny

Panel obejmuje wyłącznie serie aktywne według lifecycle, channel assignment
i assortment znanych na origin. Historia ma pełne daty kalendarzowe aktywnej
serii; confirmed zero, closed, positive i missing pozostają odrębnymi stanami.
Brak obserwacji nie przesuwa laga i nie staje się zerem. Closed history jest
potwierdzonym zerem, a closed target pozostaje w panelu z wyłączoną eligibility
kalendarza. To jeszcze nie pełna eligibility label/scoring.

36 jawnie typowanych cech obejmuje lagi 1/7/14/28, rolling mean/std/count
7/14/28, daty, znaną kategorię/brand, znany kalendarz/season, regular price
i ofertę promocji. Jeden history context jest zamrożony dla wszystkich 14
horyzontów tego samego origin. Ceny zachowują dokładne minor units i currency;
promocja opisuje znaną ofertę oraz próg ilości, bez użycia przyszłego koszyka.
Allowlist źródeł blokuje inventory, truth, target-day realized price i przyszłe
corrections. Każda cecha zachowuje dostępność, referencje i właściwe daty.

Draft inputs wiążą kalendarz, parent IDs, politykę, kod, dependencies i treść.
Typed Parquet ma jedno wystąpienie channel, jawne nullable wartości oraz
osobny audytowalny history context. Weryfikacja sprawdza fizyczne i logiczne
hashe, schematy, unikalność, PIT oraz zgodność wartości z JSON. Lagi i rolling
są ponownie obliczane z przypiętej historii, więc zmiana średniej po ponownym
wyliczeniu hashy nadal jest odrzucana. Publikacja jest atomowa, wyłączna
i nie nadpisuje poprzednich bajtów. Odczyt/budowa mają limity wierszy, plików
i bajtów opisane w runbooku.

## Odbiór danych

[Mały smoke](04-02-smoke.json) wykonał import, curated, build/verify/rebuild
na zatwierdzonym source fixture: 2 originy 2026-07-16–2026-07-17,
1 518 aktywnych targetów, 114 history contexts i 36 cech, 40,411 s.
Krótki profil ma niedostateczną historię wszystkich wierszy; nie kwalifikuje
modelu ani minimalnej historii.

[Pełny temporal smoke](04-02-temporal.json) używa producenta AI 03
z rewizji `66ea303bbbe95ecb74cfbdab91b92afd13220e9f` w osobnym worktree,
bez zmian równolegle rozwijanego AI 06. Profil seed 42, end date 2026-07-31
odtworzył dokładnie zaakceptowane source/snapshot/curated IDs AI 03.

- Wszystkie 60 originów 2026-05-19–2026-07-17, horyzonty 1–14.
- 20 076 aktywnych targetów z 20 160 potencjalnych; 1 440 history contexts.
- 40 257 aktywnych history points: 29 887 positive, 4 452 zero,
  4 478 closed i 1 440 missing.
- 252 target rows mają draft insufficient-history; pozostają jawnie oznaczone.
- 18 008 targetów eligible według kalendarza; 2 068 known closed.
- Identyczne IDs oraz bajty po rebuild; niezmienione source i curated.
- 351,458 s za import/curated i dwukrotny build/verify. To nie benchmark
  treningu ani deklaracja zasobów dla większego źródła.
- 0 wywołań AWS; status modelu `not_ready`.

Lag 1 jest missing we wszystkich targetach obu profili: observations dnia D
są dostępne dopiero o północy D+1, po cutoff 23:59:59 UTC. Pipeline zachowuje
tę rzeczywistą lukę zamiast przesuwać cutoff lub używać innego dnia.

## Walidacja

Testy obejmują dokładne daty lagów i rolling, zerowe oraz zamknięte dni,
missing bez przesuwania, cold start, granice dostępności co do mikrosekundy,
niekompletne dane, przyszłe corrections/plany, promocje anulowane i progi
ilościowe, brak planów/kalendarza, niejednoznaczne mapowania, zakazane źródła,
duże kwoty bez utraty precyzji, odczyt Parquet, powtarzalność publikacji
i uszkodzenie lub semantyczną zmianę artefaktu.

**825 testów przeszło w 672,02 s**, w tym 31 przypadków nowego zakresu.
Ruff/format, strict mypy (127 plików), schemas, skany katalogu i historii
Gitleaks, handoff, import/reimport, curated/rebuild, calendar, wheel/sdist
i Compose config przeszły. [Odłączony wheel](04-02-wheel.json), uruchomiony
poza checkoutem przez Python isolated mode, zawiera nowe schemas, odtwarza
te same calendar/input IDs oraz 1 518 targetów i 114 history contexts.
Zwykły odczyt Parquet potwierdza unikalne kolumny, w tym pojedynczy channel.
`make check` obejmuje obowiązkowy `forecast-features-check`;
limit czasu joba checks zwiększono z 15 do 20 minut na rozszerzoną regresję
oraz bramki. Wszystkie dotychczasowe kontrole pozostają wymagane.
Wszystkie bramki `make check` i `make secrets` wykonano lokalnie;
regresja działała równolegle z pozostałymi kontrolami.

## Granice odbioru i następny zakres

To panel i cechy wejściowe, bez formalnego feature_set_id/split_id,
fitted preprocessing, train folds, label maturity, fallback, baseline,
treningu, backtestingu i kwalifikacji modelu. Polityka minimum 28 aktywnych
dni oraz 7 znanych jest jawnie przypiętym draftem do kwalifikacji 04.3.
Nie uruchamiano nowego Compose/persistence, ponieważ ten zakres nie zmienia
runtime DB; sprawdzono Compose config i regresję. Nie ma publikacji, PR,
zdalnego Required CI ani merge tego zakresu na main. AI 12 pozostał osobno,
a jego budżetu AWS nie użyto.

Kolejny zakres: **04.3 — formalne manifests cech/splitów oraz kwalifikacja**.
