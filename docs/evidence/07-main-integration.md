# AI 07 — pełna integracja z main

Integracja obejmuje kompletny AI 07 w dwóch repozytoriach:
[AI PR #24](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/24)
i [source PR #97](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/97).
Bazami są AI `2565a216fa7807a756387dcbaa408d6ccc07840d` oraz source
`684f18f999ce6bddc5aa83bf6d7fa7aaef9fc832`; zawarte w nich AI 05 i AI 08 są zachowane.

## Zgodność obu strumieni

Migracja `0023_ai07_ai08` łączy istniejące revision
`0022_anomaly_evaluations` i `0021_stockout_jobs`. Nie przepisuje dawnych migracji:
świeża baza wykonuje oba zestawy, a baza z jednym strumieniem uzupełnia drugi.
Gotowość API wymaga wspólnej końcowej rewizji. API/OpenAPI i grant validators
zawierają jednocześnie anomaly i stockout; stockout zachowuje fizyczny scope,
anomaly scope sprzedaży. Pełny v12/stockout backup/recovery i native anomaly
OCI pozostają obowiązkowymi zadaniami Required CI.

Źródło zachowuje format 2.7 z opcjonalnymi przyszłymi planami, osobny format
anomalii 2.8 i zadeklarowany prospektywny profil stockout. Walidator rozróżnia
wersję generatora anomalii od zwykłego/planning inventory i odtwarza obie
kontrole, scenario oraz forecast watermarks. Zwykłe snapshoty 1.1 zachowują
zgodność wcześniejszych schemas; snapshoty 1.2 zachowują kontrakt anomalii.

## Zamrożone dowody i pełny CI

Fit `fcd09f68187b8d21e9dff756a24688b790221023` i source kwalifikacji
`48439ebd9515dc1c7adc633609bbe33d1657c3df` pozostają historycznymi referencjami.
Modele, selekcje, progi, finalne oceny i kapsuły nie są dopasowywane ani zmieniane.
[Manifest](07-ready/capsules.json) i [ocena v4](07-v4-final-qualified.json)
wiążą niezmienione artefakty; nieudane v2/v3 zachowują swoje wyniki.

Required CI zbiera wszystkie aktualne testy i dzieli pełną kolekcję na cztery
rozłączne grupy; brak lub błąd dowolnej grupy blokuje `required-result`.
Wszystkie dotychczasowe `make check`, security, persistence i OCI gates są zachowane.
Pełne `ready` wymaga zielonego CI aktualnego HEAD każdego PR-u, normalnego
scalenia do chronionego `main` oraz zielonego Required CI obu merge commitów.
Bieżący zapis SHA/runów znajduje się w podlinkowanych PR-ach i zastępuje
wcześniejsze historyczne odbiory gałęzi w kwestii publikacji na `main`.

Zakres kwalifikacji pozostaje `synthetic_ai_07_portfolio_v4`. Trwałe ACK/DLQ,
projekcja w RetailOps i UI mają odbiór w AI 10; nie są zaległością AI 07.
