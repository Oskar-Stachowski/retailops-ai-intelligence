# AI12 — przygotowanie przed płatnymi testami, 2026-10-08

[Draft PR32](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/32)
zawiera natywny runtime: osiem adapterów, kwalifikowany pin AI11 oraz ograniczony
chat Sonnet i query embeddings Titan. Konfiguracja proponowana celowo odrzuca
uruchomienie bez niezależnie zaakceptowanych tras.

[Lokalny receipt](12-prepaid-local-acceptance.json) zachowuje przypięcia i zakres
testów. Kod po integracji main `bc50733` przechodzi `ci-checks`, 49 testów runtime
i golden wheel 50/50 przy zablokowanej sieci. Poprzedni checkpoint niezmienionego
kodu Assistant przeszedł 728 testów bezpieczeństwa i 21 przypadków PostgreSQL.
Zmiana main dotyczy kampanii AI09; Required CI sprawdza cały zintegrowany kod.

Indeks AI11 odtworzono na własnym SQL z istniejących prawdziwych wektorów Titan:
44 ocenione przypadki, 39 spełniających pełne wymagania indywidualne,
9/9 krytycznych, recall@5 0,85294, MRR 0,66127, poprawność cytatów 1,0.
Przyjęte progi zbiorcze są spełnione. Nie wykonywano nowych embeddings ani
odbioru odpowiedzi Assistant. Z sześciu osobno sprawdzonych pytań dokumentacji
pełne wymagane dowody mają trzy; pozostałe zachowują `insufficient_evidence`.

[Workflow bez AWS](../../.github/workflows/ai12-source-prepaid.yml) sprawdza
Assistant HTTP, natywny odczyt Source SQL, atomowy outbox, publisher z osobnym
lockiem, broker, konsumenta Source, SQL/API i zbudowany UI w Chromium.
LLM jest jawną atrapą, obserwacja sprzedaży jest wymyślona. Test obejmuje
oryginalne bajty, deduplikację, scope, read-only i cofnięcie dostępu.
Aktualny wynik i bezpieczne receipts podają checki PR32.
Przygotowanie testu i uruchomienie joba nie dowodzą jego zaliczenia.

Próba zdalna `37738941272` wykryła błąd SQL tworzenia roli z `SET`.
Próba `37739332430` po poprawce opublikowała zdarzenie przez właściwy publisher,
lecz oczekiwała `sent` zamiast faktycznego `delivered`. Provisioning i asercję
poprawiono. Required CI `37739332433` zatrzymał się na agent-evaluate po zmianie
main. Zachowano opublikowaną rodzinę `.prepaid.v1` i utworzono `.prepaid.v2`.
Historycznych failures, budżetów ani receipts nie nadpisano.

Płatne wywołania tego przygotowania: **0**. Wykonano sześć bezpłatnych odczytów
dostępu i profili modeli, bez zmian uprawnień lub subskrypcji;
[receipt](12-prepaid-controlplane.json) zachowuje wynik. CI nie otrzymuje
poświadczeń AWS i nie wywołuje AWS. Publiczny cennik odczytano przez HTTPS;
rezerwy i właściwe SKU zapisano w [planie v2](12-prepaid-test-plan-v2.json).

Nie znaleziono niezależnego odbioru 50 pytań i 26 tras AI12 w dostępnej historii
obu repozytoriów ani lokalnym odbiorze AI11. Pozostają `proposed` i
`human_approval=false`; przygotowano [pakiet przeglądu](12-prepaid-label-review-packet-v2.json).
Przed płatną kwalifikacją pozostają odbiór etykiet/reguł, nowa zgoda na budżet,
właściwe bieżące Source/model pins oraz braki dowodów dokumentowych.
Native-v2 pozostaje nieodebrany przez Source; canonical nie jest zastępowany
development v12. Brak heartbeat/Kafka lag pozostaje jawny.
[Instrukcja wznowienia](../ai12-paid-qualification.md) opisuje kroki.
AI12 pozostaje **in_progress**, PR32 jest draft.

Pracowano w odrębnym worktree AI12 i własnej przypiętej kopii Source.
Oryginalne checkouty AI `abf3f69a` i Source `599735a5` zachowały HEAD i czyste
pliki śledzone. Własny PostgreSQL zatrzymano. Wspólnego Docker, Compose,
usług i kolejki CI sąsiednich sesji nie zmieniano.
