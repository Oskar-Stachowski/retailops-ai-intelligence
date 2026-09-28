# Kontrola podobnych treści korpusu

`corpus-review` odtwarza [fragmenty](knowledge-chunks.md) z jawnych granic
rejestru i przypiętych obiektów Git. Raport wskazuje dokładnie powtarzające się
treści oraz pary podobne leksykalnie, oddzielnie dla dokumentów i fragmentów.
Służy przeglądowi redakcyjnemu: nie usuwa, nie scala i nie zatwierdza źródeł.
Rzeczywisty rejestr i etykiety nadal mają status propozycji.
[Odbiór](evidence/11-similarity.md) podaje wynik kontroli przypiętego korpusu
i niezależne porównanie wszystkich par.

## Uruchomienie

Z katalogu AI, z RetailOps obok:

```bash
mkdir -p .local/rag
.tools/bin/uv run --locked retailops-ai corpus-review \
  --registry knowledge/corpus.v1.json \
  --chunker-config knowledge/chunker.v1.json \
  --similarity-policy knowledge/similarity.v1.json \
  --retailops-repo ../retailops-cloud-native-platform \
  --ai-repo . \
  --output .local/rag/similarity-review.json
```

Polecenie sprawdza źródła, checksumy, dowody metadanych i pełny manifest fragmentów
przed analizą. Niezacommitowane zmiany nie stają się źródłem. Nie potrzebuje
settings serwisu, DB, AWS ani modeli. Wypisuje tylko liczniki i ID raportu;
raport zawiera referencje/metadata, bez tekstu dokumentów i bez odpowiedzi LLM.

Zapis jest atomowy do nowego pliku `0600`. Istniejący plik nie jest nadpisywany.
Exit 0 oznacza kompletny raport, również gdy wymaga on przeglądu; exit 2 oznacza
błąd i brak nowego wyniku. Przy kolejnej analizie wybierz nową nazwę pliku.

## Zakres i interpretacja

[Polityka](../knowledge/similarity.v1.json) przypina normalizację Unicode
NFKC/casefold oraz wersję Unicode 14.0.0. Słowem jest sekwencja liter/cyfr;
interpunkcja i podkreślenia rozdzielają słowa. Near comparison używa zbiorów
kolejnych trójek słów i Jaccarda: liczby wspólnych trójek podzielonej przez liczbę
trójek w ich sumie. Domyślny próg wynosi **0,80**, minimum **12 słów**.
Raport zapisuje licznik/mianownik i wynik w punktach bazowych, bez progu float.

Dokładne grupy porównują SHA-256 bajtów treści przed tą normalizacją.
Krótkie treści nadal podlegają kontroli dokładnych powtórzeń; brak near score
jest jawny w `eligible_for_near=false`. Puste dokumenty bez treści retrieval
pozostają w coverage i nie są zgłaszane jako duplikaty.

Analiza dokumentu dotyczy kolejnych unikalnych fragmentów po pominięciu
nawigacji/nagłówków przez chunker, połączonych znakiem LF. Nie oznacza zgodności
całych plików ani nagłówków; pełne duplikaty dokumentów nadal raportuje
[manifest korpusu](knowledge-corpus.md). Analiza fragmentu zachowuje heading path,
typ bloku i wszystkie cytaty do konkretnego SHA/ścieżki/linii.

`content_groups` zachowują wszystkie unit IDs, dokumenty, statusy, access classes
i fact scopes. `near_pairs` wskazują dwie grupy checksum oraz różnice metadata.
Jeden tekst pod dwoma zakresami dostępu pozostaje dwoma źródłami.
Podobne zdania z negacją lub inną liczbą mogą wymagać zachowania obu wersji.
Pary nie są łączone w przechodnie klastry; A podobne do B i B do C nie oznacza
podobieństwa A do C.

Brak par przy tym progu oznacza wyłącznie brak kandydatów tej metody leksykalnej.
Nie wyklucza parafraz, sprzeczności, przestarzałych faktów lub błędnej klasy dostępu.
Każdy raport zachowuje `activation_allowed=false`, również bez kandydatów.
Decyzja redakcyjna wymaga wskazania źródeł i ponownego builda po zmianie rejestru;
raport nie nadaje statusu implemented/verified i nie tworzy CorpusApproval.

## Powtarzalność i limity

ID raportu obejmuje corpus/config/chunk manifest, politykę, metadane, cytaty
i wyniki; nie obejmuje czasu wykonania lub lokalnej ścieżki. Zmiana SHA aktualizuje
cytaty i raport, zachowując ID niezmienionych fragmentów oraz grup treści.
[Schemas](../contracts/knowledge/v1/similarity-report.v1.schema.json) i
[polityka](../contracts/knowledge/v1/similarity-policy.v1.schema.json) są wersjonowane
i sprawdzane przez `contracts-check`.

Polityka ogranicza łącznie cechy obu analiz do 250 000, a na każdą analizę:
250 000 par kandydackich, 2 000 000 odwiedzin list wspólnych cech i 10 000
raportowanych par. Dokładne kopie są grupowane przed near comparison.
Przekroczenie któregokolwiek limitu przerywa cały raport, bez cichego pominięcia
par lub przycięcia outputu. Nie jest to gwarancja czasu dla dowolnego korpusu.

Następny zakres: odświeżenie/przegląd przypiętych źródeł, statusów/dostępu
i golden labels, następnie profil użytkowy i kwalifikacja jakości etapu 11.
