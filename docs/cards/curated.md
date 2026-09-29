# Karta danych — curated 1.0.0

Właściciel transformacji: RetailOps AI Intelligence. Rola/classification:
curated, 25 tabel zatwierdzonych facts/plans source 2.6.0. Nie zawiera truth,
raw events, outputs generatora, features ani labels. Parent source/snapshot
IDs, source config/watermarks i exact transform fingerprints są w manifestach.

Curated grain zachowuje źródłowe klucze. Daily observations mają
business date/product/selling location/channel, history dodatkowo version.
Price/promotion/assignment/route/assortment zachowują klucz, wersję i pełny okres.
Record SHA i mapping IDs utrzymują lineage do źródłowych faktów; legacy store
nie zastępuje selling/stock location. Brak mappingu lub data gap nie jest zerem.

Quantity oznacza saleable pcs; pack g/ml/pcs jest osobnym atrybutem produktu.
Money zachowuje decimal128(38,2) i currency, bez FX. UTC timestamps mają precyzję
mikrosekund, business dates date32 i timezone UTC. PL/DE calendars pozostają
źródłowe; nie wprowadza się nowej konwersji stref ani własnego kalendarza.

Wszystkie quantity revisions pozostają w daily_demand_versions. As-of wybiera
wersję dostępną w origin; realized price/returns i final observation nie są
automatycznymi future features. Closed, explicit zero i inactive exclusions
pozostają różnymi stanami. History/return tail nie jest obcinany do end date.
Brak recorded availability dla legacy/static rekordów pozostaje jawny.

Kanonizacja curated exact 1.0.0 jest niezależna od układu Parquet i source
canonical 1.6.0. Descriptor ma osobny curated ID, rodziców, transform
code/schema/lock/config hashes, liczebności, ranges, checksums i readiness.
Każdy plik ma osobny byte hash; manifest ma transport checksum bez cyklu ID.

Zbiór z dowolnym odrzuceniem ma forecast_source failed i oddzielny
curated-rejected bundle. Quarantine zachowuje row/grain/hash/reason;
zwykły odczyt wymaga ready dataset. Inventory false; model/anomaly/stockout/replay
not_ready. Dane są syntetyczne, smoke nie jest dowodem jakości modeli.

Generated curated/rejected pozostają poza Git. Parent import i wykorzystane
immutable IDs muszą pozostać dostępne dla lineage; cleanup nie jest automatyczny.
[Runbook](../curated.md) i [pomiary](../evidence/03-05-curated.md) opisują
faktycznie wykonane profile, liczebności, czasy i ograniczenia.
