# Portfolio AI 09 — nowe porównanie i zamknięte AI 07–08

Obowiązuje [zatwierdzony zakres](ai09-portfolio-ready.md). Ta strona zestawia
wyniki o różnych zakresach danych; nie udaje jednego wspólnego testu.
Ocena forecastingu AI 09 jest **ukończona, publikacja w odbiorze**. Historyczne etapy
AI 07–08 pozostają **ready** w swoich zaakceptowanych zakresach.

| Zastosowanie | Przypięty model | Zakres dowodów | Bieżąca decyzja |
| --- | --- | --- | --- |
| Forecasting | Baseline/RF/HGB/TensorFlow; [raport](ai09-forecast-portfolio-report.md) i [karta TF](cards/tensorflow-portfolio.md) | Nowy mniejszy profil, trzy zakończone seedy final; po 19443 wspólne klucze i 17437 kwalifikowanych etykiet | TF odrzucony przez zamrożoną regułę; bez nowej promocji lub zmiany championa |
| Anomaly — AI 07 | Seasonal residual; referencyjny Isolation Forest | Odebrane `synthetic_ai_07_portfolio_v4`, 56/56 oryginalnych bramek dla obu modeli; zapisane karty i kapsuły | Zachować historyczne rozstrzygnięcie |
| Stockout — AI 08 | LogisticRegression with_upstream + conditional sigmoid C10 | Sześć źródeł, 9296 kwalifikujących punktów, 90 passed, 3 zaakceptowane warnings, 0 blokad | Zachować model, kalibrację, progi 25/50/90% i capacity top50% |

## Tożsamość i kontrola artefaktów

[Manifest konsolidacji 09.128](evidence/09-128-historical-portfolio.json)
przypina dokładne pliki i sumy SHA256, pełne identyfikatory modeli i kart,
historyczne commity, źródła, polityki oraz odbiory lifecycle.

2026-10-13 sprawdzono rozmiar i SHA256 obu oryginalnych kapsuł AI 07,
wewnętrzny model względem kwalifikacji, kwalifikację względem receiptu oraz
zgodność identyfikatora modelu z kartą i manifestem kapsuł. Dla AI 08
sprawdzono dokładne bajty karty i raportu jakości względem oryginalnej
kwalifikacji oraz zgodność polityki, kampanii i kwalifikacji z odbiorem modelu.
To kontrola powiązań zapisanych artefaktów, bez nowych odczytów etykiet,
treningu, kalibracji, oceny final lub promocji.

AI 07: [odbiór](evidence/07-completion.md),
[połączenie z main](evidence/07-main-integration.md),
[kapsuły i karty](evidence/07-ready/capsules.json).
Karty są w `primary.zip::model_card.json` i `reference.zip::model_card.json`,
a pełne oryginalne modele w `model.json` tych samych kapsuł. Manifest 09.128
wiąże dokładne bajty tych elementów, bez kopiowania dużych artefaktów.

AI 08: [zamknięcie](evidence/08-28-final-ready.md),
[pełna karta](evidence/08-26-model-card.json),
[jakość](evidence/08-25-final-campaign-results.md),
[rzeczywisty MLflow/lifecycle/batch/API](evidence/08-27-final-serving-acceptance.md).
MLflow run odbioru: `f4f7493bfff343a1bcf3d8d6e6953ac4`.

## Granice porównania

- Dane są syntetyczne; brak dowodu uogólnienia na rzeczywistego detalistę.
- Nie łączymy metryk anomaly, stockout i forecastingu w jedną liczbę jakości.
  Nie łączymy też zależnych światów i zachodzących okien w niezależne epizody.
- AI 07 ma małe próby niektórych typów epizodów. Jego historyczny zakres
  obejmuje 128 dni, 12 produktów, 2 pary sprzedaży i 2 lokalizacje zapasu;
  karty zachowują chronologię oraz wszystkie ograniczenia.
- AI 08 zachowuje trzy wcześniej zatwierdzone ostrzeżenia kalibracji małych
  kategorii. Pierwotne niespełnione ścisłe kryterium nie staje się passed.
  Koszty FP/FN są jednostkami ilustracyjnymi, nie oszczędnościami pieniężnymi.
- Ważność historycznej kapsuły serving AI 08 upłynęła 2026-10-06.
  Nie zmienia to odbioru etapu; ta konsolidacja nie odnawia ważności kapsuły,
  danych ani zgody na nową promocję. Odbiory usług odbyły się w środowiskach
  izolowanych, bez wdrożenia produkcyjnego.

Nowy [raport forecastingu](ai09-forecast-portfolio-report.md),
[karta](cards/tensorflow-portfolio.md) i [manifest końcowych wyników](evidence/09-136-portfolio-final-complete.json)
wiążą wykonane oceny. TF ma znormalizowany bias średniej od −23,06% do −42,74%
między seedami; nie spełnia limitu 10%. Nieobecne grupy historycznego wolumenu
zero/high pozostają `not_ready`. Odrzucenie TF nie zmienia historycznych
kwalifikacji pozostałych zastosowań. Pełny odbiór publikacji należy do R8.
