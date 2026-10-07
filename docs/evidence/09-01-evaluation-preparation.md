# AI 09.1 — przygotowanie protokołu ewaluacji

Data: 2026-10-04. Status AI 09: **in_progress**.

Przyrost dodaje wykonywalny, wersjonowany plan dla forecast/anomaly/stockout,
osobne seedy danych i inicjalizacji, ograniczoną propozycję eksperymentów
TensorFlow, immutable manifest i kontrolę wspólnych kluczy prognoz.
[Runbook i granice](../evaluation-preparation.md) określają dalszy zakres.

Normatywne dokumenty RetailOps przypięto do `599735a5fd13328e05f6100a07f644f4676e1ced`;
reguły forecast quality v2 i bazę AI do `f4ae14fe6588b91506383a709b5a0185208a4ea6`.
Przy utworzeniu planu bajty każdego z siedmiu plików porównano z blobem Git
tego commitu. Runtime potwierdza wyłącznie zgodność bajtów specyfikacji,
bez przełączania lub wymagania konkretnego obecnego HEAD innych sesji.

Kontrole lokalne i pakietowe są zapisywane po wykonaniu w
[wersjonowanym receipt](09-01-evaluation-preparation.json).
Zdalne CI będzie dotyczyć osobnego draft PR i jego dokładnego commitu.

**Odbiór lokalny passed:** 51 nowych testów w 0,29 s i pełne `make ci-local`
z 1781/1781 testów w 1825,45 s. Ruff/format obejmował 543 pliki, strict Mypy
325 modułów. Wszystkie checkery runtime/artefaktów/kontraktów, wheel ze sdist,
Compose config oraz skany historii i katalogu przeszły. Zbudowany ponownie
wheel jest byte-identical z pakietem użytym w odbiorze.

Cztery świeże procesy native/wheel odtworzyły jeden preparation ID i identyczne
4298 bajtów. Czasy prepare: 0,223 / 0,225 / 0,214 / 0,216 s; to czas metadanych,
nie treningu lub kampanii. Verify zwracało 0, preflight 3 i `not_ready`.
Siedem plików specyfikacji pozostało niezmienionych, namespace producenta
był niedostępny. Testy obejmują także równoczesną publikację, uszkodzony staging,
resealed metadata, duplikaty JSON, symlinki, zmianę normatywnych reguł oraz
niepełny zbiór kluczy prognoz.

Pierwszy pełny przebieg przerwano po błędach sandboxa przy enumeracji procesów
przez `sysctl`. Ten sam test z limitami wątków przeszedł poza sandboxem;
cały wymagany przebieg ponowiono tam z exit 0. Historyczny nieudany przebieg
jest osobno zapisany i nie kwalifikuje przyrostu. Nie zmniejszono żadnej bramki.

Nie trenowano TensorFlow, nie generowano nowych danych, nie odczytywano
outcomes portfolio final testu i nie zmieniano registry/aliasów/serving.
AI 07 i AI 08 nie mają jeszcze przypiętych końcowych odbiorów dla AI 09.
Preflight pozostaje `not_ready`, a dostęp do final testu oraz promocja false.
Zapis planu nie oznacza zamrożenia kompletnego, zatwierdzonego protokołu kampanii.
