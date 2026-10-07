# AI 08 — odbiór zasobów kompletnego pipeline

[Pomiar](../../scripts/measure_stockout_pipeline.py) obejmuje producer checkout,
generację, kwalifikację, oba eksporty, oba importy, curated, cechy 2.2,
etykiety 2.0, upstream 2.1, temporal 2.1, rzeczywiste development i sześć fitów.
Nie dodaje modelu do registry i nie wykonuje końcowej oceny testu.

Generator działa w oddzielnym procesie i tymczasowym lokalnym klonie przypiętego
producenta `08639e9`. Eksporter wymaga własnego `data/generated`, dlatego pomiar
nie zapisuje danych w dotychczasowym checkout ani w zamrożonych wejściach.
Konsument używa niezmienionego kodu `4faaf4b`; jego źródła, kontrakty i lockfile
są sprawdzane przed startem. Korzenie producenta `data`, `ml`, `retailops`
muszą być nieimportowalne w procesie konsumenta.

## Kontrola wykonania

Monitor co 0,2 s mierzy RSS własnego procesu i wszystkich jego potomków,
logiczne i zaalokowane bajty całego scratch, liczbę plików oraz wolny dysk.
RSS jest próbkowanym maksimum; nie jest gwarantowanym maksimum między próbkami.
Czas całego przebiegu zawiera start procesów, importy, monitoring i replay.
Raport zachowuje CPU rodzica i potomków osobno, czasy etapów, rzeczywiste
rozmiary wejść/wyjść, identyfikatory i skróty logów. Instalacja zależności
pozostaje poza przebiegiem; lokalny checkout producenta jest wewnątrz.

Przekroczenie budżetu zatrzymuje wyłącznie własną nową grupę procesów,
również gdy lider zakończył się, a potomek ignoruje SIGTERM. Nieukończony
przebieg ma niezerowy exit. Brak kompletnego receipt po exit 0 również
jest błędem. Nieudany scratch i mała próbka są usuwane; przyjęty pilot
zachowuje swój osobny katalog do kolejnej niezależnej oceny.

## Zgodność ponownej generacji smoke

Eksport zapisuje rzeczywiste `generated_at`. Powtórna generacja tych samych
danych zachowuje source/snapshot IDs i treść tabel, ale zmienia pełny skrót
manifestu prywatnego. Etykiety wiążą ten skrót przez `input_seal`, a temporal
i development wiążą rzeczywistych nowych rodziców. Ich IDs mogą się zmienić.

Odbiór porównuje wszystkie modele, wyniki, report, klucze/cel development,
polityki, etykiety, comparison i membership. Dopuszcza wyłącznie nowe,
poprawnie wyliczone identyfikatory rodziców i ich zweryfikowane surowe seals.
Niezmienione source/curated/features/upstream IDs muszą pozostać równe.
Descriptor etykiet, jego table/window content seals i report są równe;
zmieniać się może pełny skrót prywatnego manifestu. Descriptor temporal poza
parent IDs i pełnym skrótem rodziców jest równy, w tym indexed payload seal
i liczby wierszy. Nowe IDs muszą wskazywać na rzeczywiście zbudowane pakiety.
Nie zmienia się timestampów, checksums ani starszych artefaktów.
Każdy rzeczywisty reader nadal weryfikuje własne kompletne bieżące wejścia.

## Pilot przed większą oceną

[Pierwsza konfiguracja](stockout-resource-pilot-1.json) deklaruje 4480 origin
(16 × 2 × 140), ale pozostaje niewykonana. Pomiar całego smoke, zamiast
samego assemblera, wykazał większy RSS. Ostrożny szacunek tej konfiguracji
przekracza istniejące 1 GiB.

[Rewizja 1.1](stockout-resource-pilot-1.1.json) zamraża jawny `ai-load`:
14 produktów, 3 selling locations, 2 stock locations, 102 dni, seed 42,
4284 górne selling daily rows i 2856 fizycznych origin przed eligibility.
To profil pośredni. `ai-dev` i `ai-training` zachowują swoje pełne wymiary.
Domyślne stock/supplier/scenario settings producenta pozostają niezmienione.
Metadata warmup/origin/tail dla `ai-load` wynoszą 0; nie udają metadanych
`ai-temporal-smoke`. Czasowe role, dojrzałość, 28-dniowa historia cech
i purge pozostają wykonywalnymi regułami frozen konsumenta/split policy.

Budżet pilota wynosi 2 GiB scratch, 1 GiB całego drzewa RSS i 1800 s.
To jawna rewizja wcześniejszej, nieodebranej propozycji 5 GiB/600 s:
mniejszy scratch pozwala zachować wymagane przez użytkownika **50 GiB**,
a czas obejmuje cały większy pipeline. Start wymaga co najmniej 52 GiB.
Budżet nie jest deklaracją zmierzonego wyniku. Limity części, wejść,
qualification JSON, ledgeru, baz i development arrays pozostają bez zmian.

Preflight używa odebranego pełnego smoke oraz większego z mnożników physical
i selling grid, z 20% zapasem. Estymacja nie stanowi gwarantowanego upper bound
i nie kwalifikuje baz ani modelu. Przekroczenie prognozowanego limitu blokuje
start; zgodna estymacja dopuszcza próbę z rzeczywistym monitorem i wszystkimi
dotychczasowymi bramkami konsumenta.

```sh
.venv/bin/python scripts/measure_stockout_pipeline.py \
  --producer /private/tmp/retailops-ai08-source-v2 \
  --reference-development /private/tmp/ai08-partitions-data/temporal-series-development-native-accepted.json \
  --receipt /private/tmp/ai08-whole-smoke-new-receipt.json

.venv/bin/python scripts/measure_stockout_pipeline.py \
  --producer /private/tmp/retailops-ai08-source-v2 \
  --pilot-config docs/reference/stockout-resource-pilot-1.1.json \
  --baseline-resource /private/tmp/ai08-whole-smoke-resource-stable.json \
  --receipt /private/tmp/ai08-resource-pilot-new-receipt.json
```

Wyjścia są prywatne. Retained pilot zawiera prawdziwe źródła, eksporty,
importy, curated i parents oraz `development.json` i `development-inputs.json`.
Ten ostatni ma wyłącznie train/tune/calibration. Pełny test pozostaje
membership; jego outcomes nie trafiają do wejścia ani oceny modeli.
Ścieżka odzyskania jest zapisana w receipt jako `retained_root`.

Odbiór zasobów nie zalicza niezależnej jakości ani kalibracji.
Robustness seedy/scenariusze są zapisane przed final testem; obecny pilot
obejmuje tylko seed 42 i domyślny scenariusz. Business thresholds/capacity,
zatwierdzona kampania final testu oraz promocja wymagają swoich decyzji.
Cały AI 08 pozostaje not ready do wykonania pozostałego zakresu.
