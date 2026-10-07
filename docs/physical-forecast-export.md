# Fizyczny eksport prognoz AI 09

AI 07 i AI 08 są ready. AI 09 pozostaje `in_progress / not_ready`.
Ten eksporter przygotowuje rzeczywiste wejście do dalszego treningu i oceny.
Nie inicjalizuje kampanii, nie trenuje modeli i nie otwiera końcowego portfolio.

## Jedno źródło dla cech i etykiet

`source_replay._open_verified_source_parent` jest wspólnym wewnętrznym rdzeniem
legacy i nowych runnerów. Przed wejściem produkcyjny runner musi trwale zapisać
pełną ekspozycję rodziców w swoim zamrożonym dzienniku. Rdzeń sprawdza deklarowane
limity i runtime przed pierwszym I/O, metadane i allowlisty, kopiuje całe rodzice
do prywatnego katalogu, weryfikuje typed snapshot, curated oraz kompletną ponowną
transformację. Obsługuje dispatch wersji źródeł 1.0/1.1/1.2 istniejącego importera.
Oryginały, prywatne kopie i runtime są sprawdzane ponownie; callback po wyjściu
z kontekstu nie pozwala kontynuować odczytu.

`physical_forecast._build_physical_forecast` działa tylko z aktywnym replayem
związanym z dokładną specyfikacją źródła. Buduje kalendarz oraz istniejący magazyn
cech i historii z AI 08. Cały zbiór cech jest następnie przypisany do train,
early_stopping, tune, calibration, development_evaluation i purged.
Chronologia, purge, 14 horyzontów i opóźnienie dojrzałości zachowują wcześniejszą
semantykę; role i cutoffs muszą mieścić się w rzeczywistej historii źródła.

## Pełna populacja i jakość wersji

`physical_versions.PhysicalVersionIndex` sprawdza wszystkie obserwacje i wersje,
również późne oraz spoza okien ról, przed kwalifikacją pierwszego klucza.
Indeks zachowuje tylko surowe obserwacje i historię; nie duplikuje całych ciał
kwalifikowanych rekordów. SQLite ma cache 4 MiB, dyskowe tabele tymczasowe,
zadeklarowany page limit oraz mały cache 128 grainów. Pełny digest inventory
zachowuje semantykę wcześniejszego readera.

Dowód jakości dotyczy wyłącznie dokładnej najnowszej obserwacji i staje się znany
w jej własnym czasie. Starsza wersja pozostaje bez potwierdzonej jakości.
Wybór następuje według cutoff właściwej roli, bez fallbacku do starszej,
kompletnej wersji. Brak etykiety pozostaje censored, potwierdzone zero pozostaje
zerem. Closed targets i niewystarczająca historia pozostają w coverage oraz
powodach eligibility. Purged ma fizyczne klucze i nie ma etykiet.

Każdy plik roli jest kanonicznym JSONL posortowanym po pełnym kluczu. Manifest
wiąże cechy, wersje źródłowe, runtime, całe inventory i liczniki każdej roli.
Publikacja jest fsync i atomic no-replace. Istniejący wynik może być ponownie
wykorzystany wyłącznie po pełnej weryfikacji zgodności.

## Kontrakty i granice dowodu

Nowe [kontrakty v11](../contracts/evaluation/v11/physical_forecast_manifest.schema.json)
odróżniają ten format od wcześniejszych readerów z limitem 100000 wierszy i
128 MiB. Dawne schematy i limity pozostają zachowane. Nowe ceilings nie stanowią
pomiaru ani akceptacji większego profilu. Nadal obowiązują osobne limity
istniejącego magazynu cech; eksporter ich nie omija.

`verify_physical_forecast` weryfikuje każdy klucz, jego dokładne cechy, rolę,
cutoff, dojrzałość i eligibility względem przechowanej wybranej wersji. Wykrywa
pominięcie/duplikację kluczy i zmianę plików oraz sprawdza całe coverage. To
weryfikacja spójności artefaktu. Samodzielnie przeliczony manifest nie dowodzi
pochodzenia etykiet, audytu ani świeżości źródła; produkcyjny runner musi związać
dokładny wynik z zakończoną operacją rzeczywistego replayu i dziennika.

Manifest pozostawia `campaign_audit_qualified`, `holdout_freshness_qualified`,
`resource_qualified`, `quality_qualified`, `final_test_access_authorized`,
`promotion_allowed` oraz `stage_ready` jako false. Bieżący wewnętrzny builder
jest development-only; końcowa rola wymaga osobnego jawnego kontraktu i freeze.

## Wykonany odbiór i dalsza praca

Kontrolny native producent po AI 07–08 wygenerował jawny `ai-load`: 173 dni,
4 produkty, 2 pary sprzedaży, 2 lokalizacje zapasu i seed 42, do 2026-07-31.
To odrębne dane diagnostyczne, bez statusu nietkniętego holdoutu i bez nazwania
ich `ai-training`. Snapshot publiczny 1.1 bez truth i jego curated zawierają
29285 wierszy. Fizyczny przebieg 130 originów zachował 14560 kluczy, w tym
3360 train, cztery role po 1120 oraz 6720 purged. Wśród train są 2568 eligible,
648 closed_target i 168 insufficient_history; powody mogą się nakładać.

[Receipt 09.13](evidence/09-13-physical-forecast-export.md) zapisuje końcowy
native i installed-wheel, z identycznym manifestem i 497 modułami.
Native: 302.43 s / 169.77 MiB process peak RSS;
wheel: 241.27 s / 168.48 MiB. To koszt całego
replay/build/verify tego diagnostycznego procesu, bez pomiaru całego drzewa
oraz bez kwalifikacji większego profilu. Wstępne pomiary i błędy są zachowane.

Pozostają publiczne spięcie z audytem nowej kampanii, pełny pomiar ai-dev i
ai-training, five-role training/kalibracja, osobna końcowa rola, trzy seedy,
segmenty/niepewność/koszty, trzy raporty i karty, MLflow/lifecycle i pełny odbiór
main. Gotowe odbiory AI 07–08 są wykorzystywane bez ich ponownego otwierania.
