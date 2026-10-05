# AI 08.13 — upstream 2.1 w rzeczywistym treningu

Osobny [adapter temporal 2.1](../reference/stockout-temporal-series.md) łączy
odebrane cechy 2.2, upstream 2.1 i etykiety 2.0. Zachowuje frozen decyzje
comparison/split, Parquet i trening, ale odtwarza każdego rodzica raz w
prywatnym kontekście. Dopiero kompletny wynik trafia do treningu.
Stare pakiety/ID, AI 05 i finalne v12 są zachowane. Cały AI 08 pozostaje not ready.

## Zgodność i rzeczywiste identyfikatory

Wszystkie **1632 comparison i 1632 membership**, ich pola, logical hashes
oraz reports są identyczne z v1. Rzeczywiste development mają **340 train /
135 tune / 130 calibration**. Ich wiersze, targety, coverage i PIT lineage
kategorii są identyczne. Wszystkie **6 modeli** zachowują model IDs,
pipelines, wyniki i report. To rzeczywisty assembler nowych rodziców,
bez mostka przypisującego im stare JSON IDs.

- Upstream: `upstream-partitions-sha256-00706e367ea7f92dd81865e36fb426b8b7b44587cbe029f4fe5c4c0021c51ef5`.
- Temporal: `temporal-partitions-sha256-0aaeb1f6f6d888cca13dc8fa6572975bd95f0045e970f8b388aff4cff71957ad`.
- Development: `development-sha256-8016d7a453a5bb3d718ea594a9c007ebed19e748917b11cb0e777f5a0352219a`.

131 punktów final test to tylko membership; brak wektora celów i metryk.
Sigmoid nadal ma tylko in-sample fit diagnostics. Karta 1.0 wymaga starego
feature JSON/ID; karta nowych rodziców potrzebuje własnego adaptera.

Native build/rebuild/verify/assemble i odłączony wheel są identyczne.
Zainstalowane CLI build (retry), verify i train mają exit0; train zwraca
identyczny development. **79 modułów konsumenta** pochodzi z instalacji;
rooty producenta `data`, `ml`, `retailops` są nieimportowalne.
Wynik ma **15 plików / 183189 B**, siedem par części, do 256 kluczy na batch.
573 pliki oryginalnych wejść/v1 rodziców (63417718 B) zachowują fingerprint
`8a7575f4ce9acebde85bd07ccb65c271a24654bfb59cae01a3008c58585ee6b9`.
Dodatkowe 287 plików odebranych rodziców partycji
(4052073 B) także zachowują pełne seals.

## Pomiar powtarzanego przygotowania

Trzy naprzemienne pary w świeżych procesach na tym tej samej próbce z 1632 origin
porównują **assembler development**, bez fitu i większej generacji.
Każda próbka ma ten sam digest faktycznych danych
`147d5a6b253e8082e4b8919e66f0c2664ab69f31ca706f74966ed9e0658fa5ca`.

| Mediana | Temporal 2.0/upstream 2.0 | Temporal 2.1/upstream 2.1 |
|---|---:|---:|
| Wall z importami/monitoringiem | 329.81 s | 177.06 s |
| Wall samego assemblera | 326.82 s | 174.63 s |
| CPU procesu | 309.68 s | 160.74 s |
| Peak RSS procesu (ru_maxrss) | 226.00 MiB | 226.33 MiB |
| Scratch, próbkowanie co 0.5 s | 69.25 MiB | 69.22 MiB |

Zmierzony wall jest krótszy o **46.3%**. RSS i scratch pozostają
podobne. Równolegle trwały lokalne CI/odbiór; timery różnią się między
próbkami i nie gwarantują takiej proporcji na większym profilu.
To nie odbiór całego pipeline producent→import→curated→rodzice→trening.

Odtworzenia cech: **3→1**, upstream: **2→1**, etykiet: **2→1**,
comparison/split: **4→1**. Pełne source seals i kontrole bajtów pozostają.
Każda próbka miała odrębny scratch, limit 512 MiB, limit 1 GiB RSS drzewa,
limit 600 s i kontrolę 50 GiB wolnego dysku co 0.5 s. Wszystkie zmieściły się.
Minimum wolnego miejsca: **53.97 GiB**.

## Kontrole i zakres pozostający

48 nowych przypadków wykazuje actual replay, ochronę przed zmianą ostatnich
części każdego rodzica i temporal, resealing, zmianą wcześniej odczytanej
części, zastąpieniem bazy, symlink i przywróceniem mtime. Query-only,
opt-in, purging/availability, oba typy outcome development, brak trusted
cache, limity i cleanup pozostają egzekwowane. Builder blokuje też output
powyżej 4096 plików, zanim mógłby opublikować wynik odrzucany przez reader.

Pełny `make ci-local` przeszedł: **2133/2133 testów**, 0 ostrzeżeń, 2849.97 s testów; 21 targetów, pakiet, Compose config i oba skany sekretów.
Rodzic 92b8d5f ma zielone Required CI PR 37215204714 i push 37215199223.
Nowy commit wymaga własnego CI w draft PR #14.
Wstępny błąd klasyfikacji limitu i kolejności odmowy oraz przerwany CI/odbiory
sprzed guardu output count są jawnie zapisane w [receipt](08-13-stockout-temporal-series.json).
Nie zastępują dowodów końcowego kodu.

Pozostają: faktyczny większy profil i preflight całych zasobów (rezerwa 50 GiB,
qualification JSON 4 MiB, input 500k / 64 MiB, bazy 128 MiB, 10k kluczy i 16 MiB
development nadal obowiązują), niezależna jakość/calibration, karta nowych
rodziców, zaakceptowane progi/capacity oraz lifecycle/batch/read API.
Final test i promocja nadal wymagają osobnej autoryzacji.
