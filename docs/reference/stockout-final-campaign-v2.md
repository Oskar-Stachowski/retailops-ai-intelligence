# AI 08 — zatwierdzona kampania końcowa v2

Użytkownik zaakceptował rekomendację i polecił doprowadzenie AI 08 do ready.
[Manifest v2](stockout-final-campaign-v2.json) i [zgoda](stockout-final-permission-v2.json)
wiążą konkretne źródła, kod, zależności, model oraz politykę przed oceną końcową.
Pierwotna propozycja [v1](stockout-final-campaign-v1.md) pozostaje historyczną propozycją;
nie wykonano jej końcowej oceny.

Zachowano sześć źródeł oraz 9296 kwalifikujących się membership: matching
477/473/482 i future_stress 2627/2609/2628, seedy 42/137/2026. Zamrożony LR
with_upstream z conditional sigmoid C=10 nie jest trenowany ani kalibrowany ponownie.
Progi low/medium/high/critical pozostają 25/50/90%.

[Polityka v2](stockout-final-selected-policy-v2.json) wybiera top 50% na każdym
fizycznym origin, z zaokrągleniem w górę i deterministycznym rozstrzyganiem remisów.
Wybrano ją wyłącznie na 466 wcześniej zamrożonych punktach development, według
kosztu FP1/FN5. Kategorie i magazyny nie otrzymują dodatkowego budżetu.
Pełny publiczny replay i wyniki są zapisane w [odbiorze 08.24](../evidence/08-24-approved-campaign.md).

Wymagane wsparcie pozostaje co najmniej 20 punktów i po 5 obu klas. AP musi
przewyższać częstość zdarzeń, a Brier pokonać stałą TRAIN 550/1305. ECE do 0,15
przechodzi. Wyłącznie kategoria z mniej niż 100 punktami, zaliczonym AP/Brier
i wsparciem obu klas może dostać jawne ostrzeżenie przy 0,15 < ECE ≤ 0,20.
Ostrzeżenie nie zamienia niezaliczonej kontroli kalibracji w zaliczoną: zachowuje
wartość ECE i jej flagę false. ECE > 0,20, kategoria n≥100, brak wsparcia oraz
wszystkie pozostałe błędy blokują odbiór. Całość, fizyczne magazyny, ograniczenie
zapasu i cztery wymagane scenariusze zachowują twarde ECE ≤ 0,15.

Sześć osobnych runnerów GitHub wykonuje każdy świat native i z niezależnie
zainstalowanej paczki. Wymagana jest zgodność raportów, dzienniki dostępu,
niezmienione źródła oraz limity zasobów. Każdy świat pozostaje osobnym wynikiem.
Po zaliczeniu kampanii qualifier odtwarza komplet publicznych features/upstream
i zapisuje prawdziwą kwalifikację oraz kartę. Zgoda kampanii nie uruchamia promocji
produkcyjnej. Końcowy odbiór integracji i CI pozostają osobnymi wymaganiami.
