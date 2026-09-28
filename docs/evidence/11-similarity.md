# Odbiór kontroli podobnych treści — etap 11

**2026-09-28 · raport leksykalny odebrany, przegląd redakcyjny pozostaje otwarty.**
Baza `54198c0`, gałąź `ai/rag-corpus`.
[Pomiar JSON i hashe kodu](11-similarity.json),
[instrukcja](../knowledge-review.md), [aktualny status](../STATUS.md).

## Wynik dla przypiętego korpusu

Kontrola odtworzyła z Git **20 dokumentów i 302 fragmenty**. Rejestr zachowuje
źródła AI `7940d2ee7c82dee4e99d49755fc7eb898f15b2ca` oraz RetailOps
`b8de65b53b3f27863bb11999ca6e9a5d6d41a760`; nie zostały odświeżone przez tę analizę.
Nie zmieniono rejestru, konfiguracji fake ani golden labels. Nie utworzono zgody
lub profilu użytkowego i nie aktywowano rzeczywistego indeksu.

| Kontrola | Dokumenty | Fragmenty |
|---|---:|---:|
| Unikalne treści retrieval | 20 | 302 |
| Treści z minimum 12 słów | 20 | 273 |
| Krótkie treści bez near score | 0 | 29 |
| Dokładne grupy powtórzeń | 0 | 0 |
| Pary Jaccard ≥0,80 | 0 | 0 |
| Pary sprawdzone przez niezależną pełną analizę | 190 | 45 451 |
| Pary z przynajmniej jedną wspólną cechą w implementacji | 74 | 404 |

Wynik niezależnego porównania wszystkich par jest identyczny z wynikiem
implementacji. Łącznie użyto 31 881 cech, poniżej limitu 250 000.
CLI wykonał odczyt/walidację/budowę/analizę w około 1,91 s w lokalnym checkoutcie;
nie jest to gwarancja czasu ani pomiar semantycznego retrieval.
Raport prywatny ma tryb `0600`, ID i checksum bajtów zapisane w pomiarze JSON.

`no_lexical_candidates` dotyczy tylko tej metody, progu i przypiętych źródeł.
Nie oznacza akceptacji treści, braku parafraz/sprzeczności ani aktualności źródeł.
Raport zachowuje `activation_allowed=false`.

## Kontrole implementacji

31 nowych testów przechodzi (15,29 s). Obejmują porównanie z niezależnym
sprawdzaniem wszystkich par, negację/liczby/kod, różnice statusów i dostępu,
Unicode/casefold, krótkie/puste dokumenty, powtarzane cytaty, zmianę SHA oraz
niezależność od kolejności rejestru i hash seed. Żaden kandydat nie powoduje
scalania lub usuwania fragmentów.

Testy limitów cech, par, odwiedzin i outputu potwierdzają przerwanie kompletnej
analizy, bez publikacji częściowego raportu. CLI ignoruje niezacommitowane treści,
działa mimo niepoprawnych settings serwisu, nie nadpisuje outputu i zachowuje
bezpieczne błędy przy nieznanym SHA, niepoprawnej polityce i duplicate JSON keys.
Pydantic odrzuca niespójne coverage, cytaty, metadata, score, piny i aktywację;
niezależny JSON Schema validator sprawdza politykę oraz raport.

Ruff/format, strict Mypy (86 plików), dokumentacja, knowledge snapshots
i Gitleaks dir przechodzą. Nie dodano zależności runtime.
Odbiór nie wykonuje AWS/DB ani zmiany aktywnego wskaźnika.

## Dalszy zakres

Źródła i etykiety nadal wymagają odświeżenia oraz przeglądu statusów/access/scope,
po którym należy ponownie wygenerować ten raport. Profil użytkowy, golden quality
i release manifest pozostają bramkami etapu 11; real embeddings/Bedrock/agent
są odbierane w etapie 12. Nie wykonano push ani zdalnego Required CI.
