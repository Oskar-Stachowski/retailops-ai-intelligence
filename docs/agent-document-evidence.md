# Wystarczające dowody dokumentowe AI 12

Agent może odpowiedzieć na pytanie dokumentowe, jeżeli pobrane, uprawnione
źródła zawierają wszystkie informacje wymagane dla tego pytania. Samo
znalezienie dokumentu, wysoki wynik podobieństwa albo status `verified`
nie wystarcza. [Status](STATUS.md), [odbiór](evidence/12-document-evidence.md).

## Reguła pytania

`GraphPolicy.document_rules` jest konfiguracją serwera. Każda reguła zawiera
pytanie, cel (`documentation` albo `verified_state`) i listę wymaganych
informacji. Wymaganie wskazuje jeden lub więcej dopuszczalnych dowodów:
chunk ID, SHA-256 pełnego fragmentu z metadanymi oraz dokładny cytat.
Pytania porównujemy po normalizacji Unicode NFC, wielkości liter i białych
znaków. Nie ma dopasowania słów kluczowych, podobieństwa ani domyślnych reguł.

Dla pytania „Co sprawdzają health i ready serwisu AI?” wymagane są dwie
informacje: znaczenie health oraz znaczenie ready. Gdy retrieval zwróci tylko
health, wynik to `insufficient_evidence`. Tak samo kończy się odczyt dokumentu
ze statusem `verified`, który opisuje coś innego niż pytanie.

Każdy dowód nadal przechodzi kontrolę uprawnień, celu, statusu, przypiętego
indeksu i limitów kontekstu. Reguła nie nadaje praw i nie zmienia statusu źródła.
Pełny hash wiąże też rewizję, scope, treść i metadane cytatu. Zmiana źródła
unieważnia jego powiązanie do czasu nowego przeglądu i konfiguracji.

## Wybór odpowiedzi

Katalog faktów zawiera wyłącznie dosłowne, dopuszczone cytaty (do 800 znaków),
ze statusem, scope i rewizją. Jeden cytat może spełniać kilka wymagań,
a kilka cytatów może mieć wspólną referencję. Odpowiedź ma jedną pozycję
cytowania na źródło, przy zachowaniu wszystkich twierdzeń.

Każde wymaganie musi być pokryte w pobranym katalogu i w faktach wybranych
przez model. Model nie może pominąć części pytania ani sam uznać źródła za
wystarczające. Brak kompletnego katalogu kończy się `insufficient_evidence`
bez wywołania modelu do syntezy. Błędna odpowiedź modelu przechodzi najwyżej
jedną wspólną naprawę, a potem kończy się `invalid_evidence`.

Reguł nie da się przekazać w request body ani w odpowiedzi modelu. Tekst
źródła pozostaje niezaufaną treścią, również jeśli zawiera instrukcje.

## Wersje i granice

Bieżąca polityka to `typed-facts-v2`, prompty v4, a zestaw etykiet
`agent-canonical-golden-v2`. Nazwy plików `.v1.json` określają format kontraktu.
Konfiguracja, kod, prompty, wymagania, pin i golden mają nowe wiązania hash.

[Profil testowy](../agent/graph.evaluate.fake.prepaid.v4.json) ma sześć reguł ze
**syntetycznymi źródłami**. [Przegląd autorstwa etykiet](evidence/12-document-label-review.json)
opisuje pytania, wymagania i dokumentację wykorzystaną do napisania fixtures.
To nie są zacytowane oryginalne pliki repozytorium ani fakty z działającego
środowiska. Nazwy statusów badają zachowanie kontraktu. Zestaw pozostaje
`proposed`; nie deklarujemy niezależnej akceptacji człowieka.

[Profil z właściwym indeksem AI 11](../agent/graph.fake.prepaid.v4.json) ma pustą listę
reguł i odmawia odpowiedzi dokumentowych. Podłączenie rzeczywistych źródeł
wymaga przeglądu konkretnych cytatów i nowej konfiguracji. Obsługa dowolnych
parafraz pytań wymaga przyszłego planner/resolvera i osobnej ewaluacji;
obecne testy nie potwierdzają tej zdolności ani pełnego zamknięcia AI 12.

Bieżący natywny runtime ma osobne reguły dla rzeczywistych fragmentów indeksu
AI11. [Preflight v4](evidence/12-prepaid-native-preflight-v4.json) zachowuje
ranking, wymagania i statusy oraz jawnie oznacza proponowane równoważne źródła
instrukcji startu. Synthetic fixture reguły powyżej nadal testują mechanikę
walidacji, a nie jakość rzeczywistego retrieval.
