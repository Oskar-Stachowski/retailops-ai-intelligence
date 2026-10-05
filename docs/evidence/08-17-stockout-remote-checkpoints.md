# AI 08.17 — rzeczywisty checkpoint i przygotowanie późniejszych źródeł

Rodzic `42ff595ab04d77fd6bf0faeae10884f406035413` ma zielone Required CI:
[push](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37242229498)
i [PR](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37242232270).
Oba wykonują 2337 testów, odpowiednio 2447,78 s i 2682,63 s, oraz rzeczywistą
akceptację persistence. Nowy commit wymaga własnego CI.

[Przygotowanie kohort](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37242229577)
ma ukończone seedy 42 i 137. Seed 2026 jest nadal w toku podczas tego odbioru.
Artefakt seed 42 `11318333546` ma 54045542 B i SHA256 ZIP
`71816e3817709c11b1177e4695f59ba1abd5da69a979b83bde3dd1928b3f0634`.
Pobranie zweryfikowało ZIP, każdy publiczny plik i digest pakietu rodziców.
Pełny replay na runnerze daje identyczne natywne i instalowane wheel kapsuły:
3014555 B, SHA256 `f210b23bb62ef1b1391f7a97b4122c140d8af548316e970c376b22f33d209871`.

Wybrany `logistic_regression:with_upstream:conditional-C10.0`:
selection `stockout-selection-sha256-01832134958dc4ee8ad9ad91fab707412b28938b3e5607da2dea15a002b5828e`,
model `risk-model-sha256-15dc9b39125f0c01512f45e8fddec946ed254ee819d6f18159a300b7cb6ea518`,
calibrator SHA256 `463fca5ce911c26e7f01efda9dfd27d37725f6f481ab24d7c4f2ee3355219d0b`.
Na 466 CALIBRATION punktach AP 0,980946619023, Brier 0,063028839319 i ECE
0,044082465801; wszystkie wymagane segmenty wyboru przechodzą bez zmiany bramek.
To wynik wyboru, nie niezależny odbiór jakości.

Niezaakceptowana polityka
`stockout-scoring-policy-sha256-4c2a95fc879e54a67784e0b3f3d021572d3fc507899d3ada00670d5346e54300`
ma progi 0,25/0,5/0,9 oraz top 20% fizycznych pozycji per origin. Propozycja
daje 97 TP, 0 FP i 121 FN na tych samych danych; precision 1, recall 0,44495412844036697.
Koszty 1/5 są umowne, bez potwierdzonych oszczędności biznesowych.
Zgoda na politykę i końcową kampanię pozostaje wymagana.

Nowy [kontrakt przygotowania późniejszych źródeł](../reference/stockout-future-sources.md)
ma prospektywne profile 1.8 i osobny workflow. Nie fituje modeli i nie ocenia TEST.
Nowe duże źródła nie były jeszcze uruchomione przy zapisie tego dokumentu.
Rzeczywisty mały fixture przechodzi import, curated, cechy, upstream, etykiety
i temporal przy zabronionym assemblerze oraz fitowaniu.

**Niezależna jakość, późniejsze źródła, lifecycle/MLflow, batch/read API,
finalna kampania i ready całego AI 08 pozostają otwarte.**
