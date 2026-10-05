# AI 07 — końcowy odbiór lokalny

Stan na 2026-10-05: **`pending_required_ci`**. Pełny zakres lokalny jest odebrany;
zamknięcie etapu wymaga Required CI końcowych commitów obu repozytoriów.
Publikacja do publicznych repozytoriów czeka na zgodę właściciela.

## Zamrożona jakość

[Deklaracja v4](07-v4-experiment-declaration.json) poprzedza generację i ocenę
nowego finalnego okna. Dziewięć rzeczywistych dopasowań i porównanie obu rodzin
używają wyłącznie seed-42 train/validation. Dwie selekcje zamrożono przed
otwarciem sześciu finalnych przypadków: seedy 42, 137, 2026 × demand/physical.
Historia to 128 dni, 2025-01-01–2025-05-08; zakres ma 12 produktów,
dwie pary sprzedaży i dwie fizyczne lokalizacje zapasu.

[Wynik finalny](07-v4-final-qualified.json) zachowuje rzeczywiste modele,
oryginalne czasy fitu, konfiguracje i hashe wszystkich wejść oraz raportów.
Obie wersje przechodzą **56/56** pierwotnych bramek, bez odstępstw.

| Metryka | Seasonal residual — primary | Native Isolation Forest — reference |
|---|---:|---:|
| Precision | 0,964286 | 0,956044 |
| Recall obserwacji | 0,900000 | 0,725000 |
| Fałszywe alarmy / 1000 clean | 0,962232 | 0,962232 |
| Precision high severity | 1,000000 | 1,000000 |
| Recall epizodów | 32/33 | 33/33 |
| Evaluable coverage | 4277/7392 | 4277/7392 |

Obie wersje mają cztery false positives na 4157 ocenialnych clean observations.
Unknown truth 2232 i insufficient inputs 883 pozostają jawne. Zaliczono bramki
każdego typu, segmentu, seeda/scenariusza i minimalnej liczebności.
[Nieudany final v2](07-v2-final-not-ready.json) i
[nieudany final v3](07-v3-final-not-ready.json) zachowują statusy i wyniki.
Powtarzane syntetyczne eksperymenty są skorelowane; kwalifikacja dotyczy tylko
`synthetic_ai_07_portfolio_v4`.

## Rzeczywisty lifecycle, batch i odczyt

[Raport OCI](07-ready/oci-acceptance.json) dotyczy konsumenta
`5be04b19ee2890ea80a413dd92b7229eea7dff52` i zamrożonego źródła
`48439ebd9515dc1c7adc633609bbe33d1657c3df`. Rzeczywisty obraz aplikacji ma digest
`sha256:e8a2822a707db40fbe82eea77d6d3ed33c7c7b2fda9c317d4c2ba79bddb22ade`.
Test zbudował obrazy, uruchomił własne PostgreSQL 16/MLflow i wszystkie migracje.
Nie używał atrap registry, persistence ani HTTP.

Siedemnaście kontroli obejmuje prywatne uwierzytelnione CLI, rejestrację,
promocję primary i reference, awarie po utworzeniu wersji i zapisie aliasu,
rzeczywistą rywalizację blokady, recovery bez duplikatu, odrzucenie trzeciej
wersji, blokadę promocji odrzuconej wersji oraz rollback dokładnego modelu i obrazu.
Zmiana zewnętrznego aliasu blokuje nowe decyzje i nie przepina przyjętego release’u.

Batch zweryfikował komplet publicznych rodziców bez importu producer truth,
załadował zatwierdzony model i opublikował atomowo **1232 wyniki**. Powtórzenie
prywatnego zainstalowanego CLI zwróciło ten sam batch; logiczne ponowne użycie
zachowało pierwotne czasy. Celowy rzeczywisty błąd INSERT nie pozostawił
częściowego batchu ani requestu. Viewer nie może uruchamiać zapisów.

Rzeczywiste HTTP sprawdziło autoryzację 401/403/404, paginację po scope,
GET anomalies/models/evaluations, niedostępność zapisów HTTP oraz uczciwe
`stale`/`unknown` dla historycznych wejść. Wymuszony SIGKILL obu usług i restart
zachowały dokładny release, wersje 1/2/3, aliasy, trzy projekcje ewaluacji,
1232 wyniki, jeden batch i dwa requesty. Projekt, kontenery i wolumeny testowe
zostały usunięte; prywatna maszyna Docker została zatrzymana.

## Artefakty i odtworzenie

[Manifest dwóch kapsuł](07-ready/capsules.json) wiąże zapisane modele, rzeczywisty
fit `fcd09f68187b8d21e9dff756a24688b790221023`, pełny numeryczny replay jakości,
model cards, bezpieczeństwo/licencje, przykład wejścia i smoke/compatibility.
Archiwa mają 480348 i 492162 bajty; pełne dowody po rozpakowaniu mają odpowiednio
10686040 i 10728596 bajtów. To dowody kwalifikacji modeli z wygenerowanymi
syntetycznymi wierszami numerycznymi i offline truth. Nie zawierają rzeczywistych
danych biznesowych ani sekretów. Eksporty source/training pozostają poza Git.
Gitleaks sprawdził osobno wszystkie 46 rozpakowanych plików JSON: zero findings.

CI odtwarza oryginalne źródło kwalifikacji z przypiętego commitu `48439eb`;
generuje świeże native source, snapshot, day coverage, DQ i niezależne cechy.
Nie zmienia modeli ani progów. Końcowy branch producenta `ai/07-qualification-source`
dodaje wyłącznie odświeżenie sprawdzonych hashy kompatybilności fast path.
Wszystkie 58 tabel, CSV, raporty, context i ledger mają zachowaną zgodność
ordinary/indexed/cached; 16 testów przechodzi wraz z negatywnym testem pin drift.
Jego dokładny commit publikacji znajduje się w manifeście kapsuł.

Odtworzenie OCI z czystym producer checkout i jego native toolchain:

```sh
uv run --frozen python scripts/check_anomaly_oci.py --producer /path/to/frozen-producer
```

Opcjonalny `--prepared-receipt` używa istniejących, ponownie zweryfikowanych
publicznych rodziców. Kontroler korzysta z osobnego projektu Compose i nie
publikuje portów usług. Pełne wejście musi istnieć; bind mounts nie tworzą
brakujących katalogów. [Runbook](../reference/anomaly-portfolio-qualification.md)
opisuje wersjonowane cechy, ocenę i granice kwalifikacji.

## Pozostała bramka

Required CI końcowego brancha AI musi zaliczyć `checks`, `secrets`, `persistence`,
obowiązkowy `anomaly-oci` i `required-result`; producent wymaga własnego pełnego
Required CI. Starsze zielone PR-y ani lokalny odbiór nie zastępują tych runów.
Po rzeczywistym sukcesie można zamknąć AI 07 jako `ready` w tym zakresie.
Trwałość brokera, ACK/DLQ, projekcje i UI RetailOps należą do AI 10.
Wdrożenie produkcyjne nie jest dopuszczone przez ten odbiór.
