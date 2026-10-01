# Odbiór AI 04 — finalna v12 z zaakceptowanymi odstępstwami

Właściciel projektu 2026-10-01 polecił:

> doprowadź AI 04 do ready. uznajemy V12 za finalne

Decyzję podjął po przedstawieniu trzech regresji MSE i rozróżnieniu odbioru
developerskiego od kwalifikacji jakości oraz wdrożenia produkcyjnego.
Następnie zatwierdził przekazanie zamknięcia z sesji v13 do sesji odbierającej.

**Decyzja: etap AI 04 — `ready`; finalny rezultat — v12.**
Jest to akceptacja właściciela z wyjątkami. Obowiązuje po przyjęciu commitu
tej decyzji przez [PR #7](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/7)
oraz zielonym Required CI chronionego `main`. Sam plik decyzji nie zastępuje
wyniku CI ani dowodu merge; te dowody udostępnia GitHub.

## Dokładny zakres decyzji

[Wersjonowany zapis](04-v12-acceptance.json) wiąże akceptację z jednym runem:
`functional-v12-run-sha256-345a725d435a477374292cb9483350fb5c50c8ba87d06668c727e0a9f964fb6b`.
SHA-256 jego manifestu to
`29bf6837ca2cb7504213168743239b037041f375763bc8f01ae28c1f6d09b26e`.

| Obszar | Zachowany wynik |
| --- | --- |
| Dane i ocena | 64/64 kohorty, 27 396 096 wierszy, 224 przekroje |
| Oryginalna jakość | 221 zaliczone, 3 niezaliczone; `not_ready` |
| Zaakceptowane regresje MSE | +0,204738%; +0,000619%; +0,043292% |
| Odtwarzanie | niezależny replay zapisanych parametrów: passed |
| Eksport | 663 pliki / 31 994 594 655 B, wszystkie kohorty |
| Weryfikacja pakietu | rzeczywisty run zweryfikowany z zamrożonego wheel: passed |
| Wdrożenie / promocja | nie objęte decyzją |

[Opis v12](../forecast-functional-v12.md) podaje kategorie i okresy każdej
regresji. Oryginalne metryki, receptury, prognozy, źródła i protokół pozostają
niezmienione. Właściciel zaakceptował odstępstwa po poznaniu wyników;
nie przedstawiamy tej decyzji jako wcześniejszego kryterium eksperymentu,
zaliczenia 224/224 ani dowodu statystycznej nieistotności. Nie oszacowano
kosztów biznesowych; test dotyczył danych syntetycznych.

## Kontrole i granice

`make forecast-acceptance-check`, także jako część `make check` i Required CI,
sprawdza SHA-256 oryginalnych dowodów, tożsamość eksportu, komplet próby,
zachowanie wyniku 221/224, dokładne trzy wyjątki, replay i weryfikację wheel.
Testy odrzucają zmianę runu, v13, dodatkowe lub pominięte wyjątki, przepisanie
metryk na zaliczone oraz rozszerzenie decyzji na promocję produkcyjną.

`stage_status=ready` opisuje zamknięcie AI 04. Pola
`forecast_model_status=not_ready` i `quality_qualification_status=not_ready`
w historycznym eksporcie nadal opisują wynik niezmienionego protokołu.
AI 05 otrzymuje oba dokumenty i zachowuje własne kontrole importu, promocji
oraz serving. Akceptacja jednego runu nie wyłącza bramek innych modeli.

Zastąpioną kampanię v13 zatrzymano przed oceną holdoutów. Nie usuwano jej
zachowanych plików, historii ani rezerwacji seedów. Workflow
[36900199207](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36900199207)
zachowuje wynik anulowania, a nie zaliczenia. Wyłączono automatyczny start
generacji; późniejsze eksperymenty wymagają nowej decyzji.

Wycofanie akceptacji polega na nowej decyzji właściciela i PR wycofującym
status etapu oraz niniejszy wyjątek. Nie zmienia się w tym celu oryginalnych
artefaktów ani raportów jakości.
