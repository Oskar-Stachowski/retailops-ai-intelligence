# Mały, zamrożony fixture snapshot 1.1

`inventory-v1_1.zip` zawiera warianty `facts` (43 tabele) oraz `private`
(43 + 12 tabel i qualification) dla prawdziwego source 2.7 wygenerowanego
w RetailOps: ai-smoke, seed 42, 10 dni, 3 produkty, 3 legacy stores, 2 stock locations.
To fixture transportu; nie pełny standardowy profil odbioru i nie odbiór modelu.

Source ID: `source-sha256-b7298e4729e3fb9897712405ad068a902e255cbf9c11ba9e16d697f8edc938ee`.
Qualification: `inventory-labels-sha256-18d7c7fd43e6d87e8b2f8cc5290743f173defaa9b677dd208c8e8358c8df2657`.
Manifesty zachowują pierwotny commit/code_state i fingerprint kodu w czasie
wytworzenia; nie przepisujemy ich na późniejszy commit odbioru.

Archiwum jest niezależne od starszego `ai-smoke-v1`. Test rozpakowuje je tylko
do private temporary directory i sprawdza limit 5 MiB oraz paths.
Wariant private pozostaje poza cechami i curated. Kolejne źródła wymagają nowych IDs.
