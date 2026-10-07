# Plan skrócenia realizacji AI 00–10

Data: **2026-10-03**, stan bazowy z odczytów około 09:09–09:30 Europe/Warsaw.
Status dokumentu: **plan bazowy z 2026-10-03; aktualizacja pomiarów i wykonanych elementów na końcu dokumentu**. Sam zapis planu nie oznacza wdrożenia pozostałych punktów.
Cel: skrócić czas od tego stanu do pełnego odbioru AI 00–10, zachowując ich wymagania.

## Jak rozumieć szacunki

**Impact oznacza skrócenie terminu zakończenia całego AI 00–10**, a nie wyłącznie
czas zaoszczędzony w pojedynczym teście lub etapie. Godziny oznaczają czas
kalendarzowy przy ciągłej pracy sesji i dostępności wymaganych zasobów.
Koszt wdrożenia podajemy jako czas pracy przypisanego strumienia; impact jest
szacunkiem netto, uwzględniającym ten koszt i możliwość pracy równoległej.

Wartości są szacunkami inżynierskimi, nie zmierzonym efektem zmian. Każdy
przedział opisuje zastosowanie danego usprawnienia względem stanu bazowego.
Po wdrożeniu innych punktów jego dodatkowy wpływ zwykle będzie mniejszy.
**Nie sumować impactów z tabeli.** Czynność poza ścieżką krytyczną może
oszczędzić zasoby, ale skrócić cały projekt o **0 godzin**.

Dla porównania przyjmujemy:

- AI 07–08 równolegle: około 12–24 godzin do zakończenia wolniejszego strumienia.
- Następnie AI 09–10 równolegle: około 30–48 godzin, z integracją i odbiorem.
- Razem bez proponowanej reorganizacji: **42–72 godziny**, punkt odniesienia
  **57 godzin**. Jest to wcześniejszy szacunek, nie harmonogram zmierzony.
- Cel po skoordynowanym wdrożeniu: **30–48 godzin**, praktyczna rezerwa
  **3–5 dni kalendarzowych** przy przerwach, poprawkach i przeglądach.

Procent w tabeli = impact / 57 godzin. To orientacyjny udział w pozostałym
czasie, a nie procent skrócenia historii projektu od AI 00.
Wynik pełnego benchmarku danych, jakość modeli i awarie CI mogą zmienić te widełki.

## Fakty stanowiące podstawę planu

| Obserwacja | Znaczenie dla przyspieszenia |
|---|---|
| AI 00–06 mają wcześniejsze odbiory; AI 04 zakończono na v12 z trzema jawnymi odstępstwami, a AI 05 ma końcowy odbiór | Weryfikować dowody i kompatybilność, zamiast ponownie implementować zakończone etapy |
| AI 07 kończył pełny replay sprzedaży/zwrotów i rozpoczął CI PR RetailOps #83 | Następne prace to odbiór danych po stronie AI, detektory i ich obsługa |
| AI 08 zakończył cechy, podział czasu i historyczny forecast upstream | Następne prace to skala, trening, kalibracja, ocena i obsługa modeli |
| Ostatnie lokalne pytest AI 08: 1816 testów w 1678,21 s, czyli około 28 minut | Pełne powtórzenia są istotnym kosztem; nie jest to czas całego CI |
| Required CI obejmuje PR, push na `main`, push na `ai/**` i ręczny start | Ograniczanie wyzwalania jest zmianą kontraktu, nie prostym usunięciem wpisu YAML |
| Limit cech AI 08: 10 000 punktów i 16 MiB; próbka 1632 punktów zajmuje 10 249 555 bajtów | Mały odbiór nie dowodzi możliwości przetworzenia pełnego profilu |
| Limit wejścia modułu etykiet: 500 000 wierszy i 64 MiB; etykiety mają też własne limity | Skalowanie trzeba sprawdzić w całej ścieżce, nie tylko w zapisie cech |
| Lokalnie odczytano około 48 GiB wolnego miejsca | Równoległość ciężkich procesów i kopie artefaktów wymagają budżetu dysku |
| Większość przerw przed „kontynuuj” była krótka; jedna trwała około 160 minut | Usunięcie tego rodzaju postoju pomaga, lecz nie wyjaśnia całego czasu pracy |

To historyczny punkt odniesienia. Przed wykonaniem planu odczytać aktualny stan.
Główny lokalny checkout przy zapisie dokumentu był na `ai/11-completion`,
HEAD `abf3f69a6a78c444b5a8a910fef8b3b43ba047a9`, 111 commitów za lokalnym
`origin/main` (`f4ae14fe6588b91506383a709b5a0185208a4ea6`).
Jego README/STATUS nie jest wystarczającym źródłem aktualnego postępu AI 07–08.
Nie przełączać ani nie aktualizować tego checkoutu tylko po to, aby wykonać plan.

## Zestawienie impactu i priorytetów

Podpunkty wykonawcze w każdej sekcji składają się na jeden impact danego kroku;
nie są dodatkowymi, niezależnymi oszczędnościami.

| Krok | Usprawnienie | Koszt wdrożenia | Impact netto na cały projekt | Udział w 57 h | Pewność |
|---|---|---:|---:|---:|---|
| P01 | Zamrożony zakres, stan i właściciele kontraktów | 0,5–1 h | 0–2 h | 0–4% | Średnia |
| P02 | Kontynuacja całych zakresów i przekazanie stanu sesji | 0,25–0,5 h | 0–3 h | 0–5% | Średnia; zależy od przerw |
| P03 | Wcześniejsze przygotowanie AI 10 | 0,5–1 h organizacji; właściwa implementacja należy do AI 10 | 6–12 h | 11–21% | Średnia |
| P04 | Wcześniejsze przygotowanie AI 09 | 0,5–1 h organizacji; właściwa implementacja należy do AI 09 | 2–6 h | 4–11% | Średnia–niska |
| P05 | Wczesny benchmark i przygotowanie skali danych | 2–6 h | 0–4 h; dodatkowo warunkowe uniknięcie 6–24 h opóźnienia | 0–7% bez rezerwy ryzyka | Niska przed pomiarem |
| P06 | Wspólne niezmienne artefakty i protokół ocen | 1–3 h | 2–6 h | 4–11% | Średnia |
| P07 | Stabilne rewizje, spójne przyrosty i praca podczas CI | 0,5–1 h | 1–4 h | 2–7% | Średnia |
| P08 | Równoległe grupy pełnej regresji | 2–4 h | 1–5 h, jeśli zwrot potwierdzi pomiar | 2–9% | Średnia–niska |
| P09 | Opcjonalne ograniczenie redundantnego CI gałęzi | 1–3 h | 0–2 h, jeśli zmiana się zwróci | 0–4% | Niska; często oszczędza głównie zasoby |
| P10 | Ograniczone eksperymenty i wykorzystanie istniejących modułów | 0,5–1 h | 2–6 h | 4–11% | Średnia |
| P11 | Budżet zasobów i ewentualny osobny worker | 0,5–1 h lokalnie; dodatkowo 1–4 h dla workera | 0–6 h, warunkowo po pomiarze | 0–11% | Niska przed pomiarem |
| P12 | Wczesna integracja i jeden plan odbioru końcowego | 0,5–1 h organizacji | 1–3 h | 2–5% | Średnia |

Najpierw wykonać P01–P02 i otworzyć P03. Następnie uruchomić przygotowanie P04
oraz wykonać P05 w strumieniu AI 08. P06–P07 wdrażać od razu w bieżącej pracy.
P08, P09 i zdalna część P11 mają bramkę opłacalności: można je pominąć,
jeżeli ich wdrożenie opóźni pozostały projekt. P10 obowiązuje przed treningiem,
a P12 zaczyna się od pierwszego zgodnego kontraktu i trwa do odbioru.

## P01. Ustalić stan, zakres i właścicieli wspólnych zmian

**Impact: 0–2 godziny netto całego projektu. Koszt: 0,5–1 godziny.**
Korzyść wynika z uniknięcia ponownego otwierania zakończonych prac i konfliktów.
Gdy takich problemów nie będzie, bezpośredni impact wyniesie 0 godzin.

Właściciel: koordynator wyznaczony spośród czterech strumieni.

Kolejność wykonania:

1. Odczytać aktualny stan sesji, worktree, gałęzi, PR i CI. Zapisać SHA obu repo.
   Nie przerywać aktywnych poleceń ani nie modyfikować cudzego worktree.
2. Sporządzić krótką tabelę AI 00–10: status, dowód odbioru, pozostały rezultat,
   zależności, właściciel, następna czynność. Historyczne `planned` w mapie
   nie przeważa nad późniejszym udokumentowanym odbiorem.
3. Dla AI 00–06 potwierdzić wcześniejsze dowody. Otwierać ponownie tylko
   konkretną regresję lub wymaganą ocenę na zmienionych danych.
4. Przypiąć AI 04 do v12 i zachować zakres zaakceptowanych odstępstw.
   Akceptacja v12 nie przenosi automatycznie wyjątków na AI 07–09.
5. Przydzielić jednego właściciela zmian każdego wspólnego obszaru:
   kontrakty wyników/zdarzeń, importer/curated, lifecycle, lockfile oraz CI.
6. Określić kolejność scalania wspólnych zmian i miejsca rozszerzeń używane przez
   pozostałe strumienie. Zmiana kontraktu musi mieć wersję i zgodny przykład.

Odbiór: każdy otwarty rezultat ma jednego właściciela i warunek zakończenia;
nie ma dwóch sesji zmieniających te same wspólne pliki bez uzgodnienia.
Pomiar: liczba konfliktów, ponownych odbiorów i godzin pracy cofniętej przez
zmianę wspólnego kontraktu.

## P02. Kontynuować pełne zakresy i przygotować przekazanie pracy

**Impact: 0–3 godziny netto. Koszt: 15–30 minut.**
To ograniczenie przerw między przyrostami; czas samych obliczeń się nie zmienia.
Już upłyniętego postoju nie zaliczać do przyszłej oszczędności.

Właściciel: użytkownik/koordynator zakresu; wykonanie przez właścicieli sesji.

Kolejność wykonania:

1. Przy bezpiecznej granicy bieżącego przyrostu ustalić dla każdej sesji pełny
   zakres do zamknięcia, dopuszczone operacje i konkretne warunki zatrzymania.
2. Uzgodnić, że pomyślne zakończenie małego PR nie kończy pracy nad całym etapem;
   sesja przechodzi do następnego niezablokowanego zadania.
3. Rozdzielić zgodę na implementację, publikację PR, scalanie oraz płatne
   obliczenia. Kontynuacja korzysta z już udzielonych uprawnień; nie rozszerza ich.
4. Przy zmianie sesji lub dłuższej przerwie zapisać: worktree, SHA, PR, ostatni
   wynik, aktywne procesy, lokalizacje artefaktów, następny krok i blokery.
5. Przed ponowieniem polecenia sprawdzić, czy poprzedni proces jeszcze działa
   lub pozostawił kompletny wynik. Wznowienie ma używać checkpointu, jeśli istnieje.
6. Stosować te zasady po przekazaniu instrukcji do właściwej sesji. Sam zapis
   tego dokumentu nie steruje innymi sesjami i nie uruchamia nowych zadań.

Przykładowa treść przydziału po ustaleniu uprawnień:

> Doprowadź przydzielony zakres do jego pełnego odbioru. Po ukończeniu przyrostu
> przechodź do kolejnego niezablokowanego zadania. Pracuj w swoim worktree,
> zachowaj uzgodnione kontrakty i bramki. Raportuj postęp. Zatrzymaj pracę
> zależną od brakującej decyzji, kosztu lub uprawnienia, a niezależne zadania
> kontynuuj. Nie ingeruj w aktywne procesy pozostałych strumieni.

Odbiór: kolejny przyrost ma jasno określony start bez konieczności ponownego
ustalania zakresu. Pomiar: suma przerw między gotowością zadania do rozpoczęcia
a jego faktycznym startem, ograniczona do zadań na ścieżce krytycznej.

## P03. Przygotować AI 10 równolegle do AI 07–08

**Impact: 6–12 godzin netto. Koszt organizacji: 0,5–1 godziny.**
Właściwe godziny implementacji są przesuwane wcześniej, nie usuwane.
Impact maleje, jeśli po optymalizacji najdłuższym strumieniem stanie się AI 09.

Właściciel: trzeci strumień, osobne worktree AI i RetailOps.
Warunek startu: mapa właścicieli P01 i uzgodniona wersja kontraktu.

Kolejność wykonania:

1. Porównać aktualne OpenAPI, legacy v1, runner, projekcje i UI z planem AI 10.
   Zapisać wyłącznie potwierdzone braki; wykorzystać odebrane elementy OPS-03/07.
2. Uzgodnić envelope/payload `retailops.intelligence.v2`: identyfikator wyniku,
   ziarno, jednostkę, origin, model/version, run/dataset IDs, freshness i scope.
3. Przygotować jawne dane testowe forecast/anomaly/stockout/suggestion oraz
   testy kontraktu. Oznaczyć ich sztuczne pochodzenie w dowodach i widoku.
4. Wdrożyć typowany klient REST i kompatybilny eksport z wymaganymi polami
   ziarna oraz dostępności. Sprawdzić stronicowanie, timeouty i błędy źródła.
5. Zdefiniować trwały snapshot oraz powiązanie z offsetami zdarzeń. Zwykła
   paginacja zmiennej tabeli ani przypadkowy odczyt końca topicu nie wystarcza.
6. Rozszerzyć istniejącą transakcję przetwarzania o domenowe inbox, projekcję
   i outbox. Przetestować duplikat, zmianę wersji faktu, awarię przed/po commit,
   niedostępną kwarantannę oraz brak przeskoczenia luki offsetów.
7. Przygotować read API i istniejące widoki UI, z jawnym źródłem, świeżością,
   rozdzieleniem heurystyk i prawdopodobieństw ML oraz kontrolą uprawnień.
8. Po odbiorze AI 07–08 podmienić wejścia testowe na przypięte rzeczywiste
   wyniki modeli. Wykonać cross-repo E2E na `ai-temporal-smoke`.

Odbiór przygotowania: kontrakty, adaptery i testy integracyjne są gotowe do
podłączenia modeli. Odbiór całego AI 10 wymaga trwałego rzeczywistego wyniku
w RetailOps API/UI i prób awarii. Sugestia pozostaje oznaczonym fixture zgodnie
z planem; rzeczywisty producent rekomendacji należy do AI 12.
Pomiar: ile godzin wymaganej pracy AI 10 wykonano przed gotowością AI 07–08
i jaka część tego nakładania skróciła datę wspólnego odbioru.

## P04. Przygotować AI 09 przed końcowym odbiorem AI 07–08

**Impact: 2–6 godzin netto. Koszt organizacji: 0,5–1 godziny.**
Wcześniejsze przygotowanie skraca koniec projektu tylko wtedy, gdy AI 09
wyznaczałoby termin albo jego opóźnienie blokowałoby wspólny odbiór.

Właściciel: czwarty strumień. Przygotowanie nie oznacza wcześniejszego
zaliczenia zależności AI 09 ani dostępu do końcowych wyników testowych.

Kolejność wykonania:

1. W osobnym środowisku sprawdzić instalację przypiętych zależności TensorFlow
   i uruchomienie na docelowym Linux CPU. Zmianę lockfile koordynować przez P01.
2. Zbudować mały model Keras z wyjściem horyzontów 1–14, obsługą nieznanych
   kategorii i preprocessingiem dopasowywanym wyłącznie na train.
3. Podłączyć zapis modelu, preprocessing, podpis wejścia i odczyt przez MLflow.
   Sprawdzić zgodność predykcji po reload w zadeklarowanej tolerancji.
4. Przygotować wspólne klucze ocenianych obserwacji, maski eligibility i adapter
   istniejącego evaluatora. Dłuższe okno TF nie może usuwać trudnych obserwacji
   tylko z wyniku challengera.
5. Na danych development wykonać ograniczony trening próbny i pomiar zasobów.
   Zamrozić budżet strojenia, warunki wyboru i limity przed końcową oceną.
6. Przygotować protokół trzech zastosowań z seedami danych `[42, 137, 2026]`,
   osobnymi seedami treningu, segmentami, agregacją i polityką dostępu do testu.
7. Po odbiorze AI 07–08 przypiąć ich zgodne dane, modele i polityki. Dopiero wtedy
   wykonać wymaganą kampanię końcową i decyzje lifecycle.

Odbiór przygotowania: wykonany trening development, reload CPU i gotowy
protokół; bez deklaracji końcowej jakości. Pomiar: czas konfiguracji i adapterów
usunięty z okresu po AI 07–08 oraz koszt zmian wynikających z ich finalnych kontraktów.

## P05. Wcześnie usunąć ograniczenia skali danych

**Impact: 0–4 godziny netto. Koszt: 2–6 godzin.**
Osobna korzyść warunkowa: uniknięcie około **6–24 godzin późnego opóźnienia**,
jeżeli ograniczenia ujawniłyby się dopiero przy pełnej kampanii. Ta rezerwa
ryzyka nie jest dodatkową gwarantowaną oszczędnością i nie wchodzi do sumy celu.

Właściciel: strumień AI 08, przy uzgodnieniu wspólnych importerów z AI 07.

Kolejność wykonania:

1. Zinwentaryzować limity od źródła przez import, curated, etykiety, cechy,
   forecast upstream, podział czasu aż do treningu i ewaluacji.
2. Przed generacją oszacować liczbę originów, rekordów ledgeru i bajty plików.
   W profilu 730 dni × 200 produktów × 4 magazyny potencjalna siatka fizyczna
   ma 584 000 punktów przed wykluczeniami; rzeczywistą liczbę ustala kontrakt.
3. Wykonać kontrolowaną próbę zwiększania rozmiaru na development, co najmniej
   dwa większe rozmiary względem smoke. Ustalić limit czasu, RAM i dysku przed
   startem. Zapisać czas, peak RSS, wiersze/s, bytes oraz rozmiar wyjścia.
4. Sprawdzić, czy koszt rośnie w przybliżeniu z liczbą punktów czy szybciej,
   np. przez ponowne skanowanie całej historii dla każdego originu.
5. Wprowadzić ograniczone partycje i indeksy po produkcie, magazynie oraz
   dostępności czasowej. W razie potrzeby użyć istniejącej infrastruktury
   Parquet zamiast jednego rosnącego JSON; zaktualizować zgodnie jego konsumentów.
6. Zachować brak wycieku przyszłej wiedzy, wszystkie wymagane obserwacje,
   uzgodnienie ledgeru, checksumy i niezależną weryfikację. Samo podniesienie
   limitów albo usunięcie wierszy nie stanowi rozwiązania.
7. Porównać nową implementację ze starą na mieszczącej się próbce: te same
   wartości i statusy, uwzględniając uzasadnioną zmianę identyfikatorów/formatu.
8. Wykonać reprezentatywny większy odbiór i skorygować ETA całej kampanii.
   Małego smoke nie przedstawiać jako odbioru `ai-training`.

Odbiór: zadeklarowany większy zakres mieści się w zmierzonym budżecie,
partycje pokrywają pełny zbiór bez duplikatów i zachowują semantykę czasu.
Pomiar: koszt na 1000 punktów, szczyt pamięci, rozmiar artefaktów oraz liczba
prac później zablokowanych przez dane. Po 2 godzinach pomiaru podjąć decyzję,
czy zmiana zmieści się w budżecie P05, czy wymaga nowego planu.

## P06. Współdzielić niezmienne artefakty i spójny protokół ocen

**Impact: 2–6 godzin netto. Koszt: 1–3 godziny.**
Korzyść występuje tylko dla rzeczywiście powtarzanych zgodnych prac.

Właściciel: właściciel danych/evaluatora wyznaczony w P01.

Kolejność wykonania:

1. Utworzyć indeks istniejących artefaktów: ID, hash, źródło, wersje schematów,
   kodu i środowiska, seed, cutoff, polityka oraz wynik weryfikacji.
2. Wskazać zgodne artefakty wspólne dla kilku odbiorów. Oddzielić scenariusze
   anomalii od zwykłych danych inventory; wspólny magazyn nie oznacza jednego
   datasetu dla semantycznie różnych scenariuszy.
3. Przyjąć klucz ponownego użycia obejmujący dane, transformację, konfigurację,
   schemat, zależności i granicę wiedzy. Zmiana któregokolwiek istotnego elementu
   unieważnia odpowiednią pochodną.
4. Udostępniać zweryfikowane wejścia tylko do odczytu. Wyniki zapisywać do
   odrębnych katalogów każdego strumienia; nie kopiować wielokrotnie dużych modeli.
5. Zachować wymagane niezależne odtworzenie i sprawdzanie integralności.
   Cache wyniku nie zastępuje kwalifikacji nowej implementacji.
6. Zamrozić jeden zgodny plan ocen AI 07–09. Raport AI 09 może wskazywać
   wcześniejszy wynik, jeśli spełnia jego dokładny protokół; brakujący zakres
   trzeba wykonać. Pochodzenie i każdy dostęp do holdoutu pozostają widoczne.
7. Dane do strojenia i końcowego testu utrzymywać oddzielnie. Wyniku testu
   nie używać do wyboru kolejnej konfiguracji. Historycznie oceniony holdout
   nie staje się ponownie nietkniętym testem po zmianie nazwy lub ścieżki.

Odbiór: każda ponownie użyta pochodna ma uzasadnioną zgodność i dowód weryfikacji;
zmiana źródła lub polityki prawidłowo wymusza przeliczenie.
Pomiar: czas unikniętej generacji/importu/budowy cech pomniejszony o koszt
weryfikacji i zarządzania artefaktami; policzyć tylko część na ścieżce krytycznej.

## P07. Testować stabilne rewizje i przygotowywać spójne przyrosty

**Impact: 1–4 godziny netto. Koszt: 0,5–1 godziny.**
Oszczędność pochodzi z mniejszej liczby powtórzeń i pracy podczas oczekiwania.
Nakłada się na P02 i P08.

Właściciel: każdy strumień, koordynator pilnuje kolejności publikacji.

Kolejność wykonania:

1. Wyznaczyć spójny rezultat małego PR, obejmujący kod, kontrakt, dokumentację
   i plan jego odbioru. Unikać zarówno publikowania każdego pliku osobno,
   jak i łączenia niezależnych dużych zmian w jeden trudny do oceny PR.
2. Podczas implementacji uruchamiać testy zmienianego obszaru oraz zależności.
   W planowanym pełnym przebiegu dopisać pomiar czasów najwolniejszych testów.
3. Przed pełnym odbiorem zamrozić kod. Nie zmieniać plików wpływających na
   identity artefaktów podczas ich generacji lub testowania.
4. Wykonać pełne `make ci-local` przed PR zgodnie z obowiązującymi zasadami.
   Testy lokalne zapisują dokładną rewizję lub hashe badanego kodu.
5. Opublikować spójny przyrost i wymagane CI. Przygotować następne niezależne
   zadanie w osobnym worktree bez modyfikowania rewizji podlegającej odbiorowi.
6. Jeśli następny zakres zależy od nieodebranego kodu, oznaczyć go jako
   przygotowanie i zachować możliwość poprawki; nie ogłaszać gotowości zależności.
7. Wyniki przypinać do badanego SHA. Zmiana kodu, która unieważnia kontrolę,
   wymaga odpowiedniego ponowienia. Aktualna polityka uruchamia CI również dla docs.

Odbiór: brak testów unieważnionych zmianą plików w trakcie wykonania;
dowody dotyczą właściwej rewizji, a wymagane bramki pozostają pełne.
Pomiar: liczba i czas powtórzeń z powodu zmiany kodu/identity oraz godziny
niezależnej pracy wykonanej podczas CI.

## P08. Podzielić pełną regresję na niezależne grupy

**Impact: 1–5 godzin netto, jeśli zmiana się zwróci. Koszt: 2–4 godziny.**
Przed pomiarem nie obiecywać liniowego przyspieszenia. Gdy inny job trwa dłużej,
skrócenie pytest nie skróci całego workflow. Nieopłacalny wariant pominąć.

Właściciel: właściciel CI, jako ograniczony zakres w jednym z istniejących
strumieni; nie tworzyć obowiązkowego piątego strumienia.

Kolejność wykonania:

1. Zebrać czasy testów przy najbliższym wymaganym przebiegu, np. przez
   `pytest --durations=30`, i oddzielnie zmierzyć joby persistence oraz pozostałe gates.
2. Zidentyfikować testy współdzielące DB, porty, pliki, globalny stan lub proces.
   Trwałe testy z brokerem i bazą pozostają rzeczywistymi testami integracyjnymi.
3. Zaprojektować 2–4 grupy o zbliżonym czasie. Dla każdej zapewnić osobne
   katalogi, porty, DB/Compose project i identyfikatory artefaktów.
4. Sprawdzić kolekcję: unia identyfikatorów testów grup ma pokrywać pełny
   dotychczasowy zestaw. Każde pominięcie lub zamierzony wspólny test opisać.
5. Przygotować spójną zmianę workflow, Makefile, walidatora i testów kontraktu CI.
   Obecny walidator wymaga m.in. `make bootstrap check`; podział musi zapewniać
   równoważne wykonanie wszystkich bramek, a nie usuwać zabezpieczenie.
6. Zachować pełne bramki pakowania, kontraktów, bezpieczeństwa i persistence.
   `required-result` ma odczytywać wszystkie wymagane wyniki i kończyć się
   błędem przy failure, cancellation lub nieoczekiwanym skip.
7. Na tej samej rewizji porównać pełny przebieg dotychczasowy i podzielony.
   Przetestować celową awarię grupy i poprawne odrzucenie przez agregator.
8. Przyjąć zmianę tylko po potwierdzeniu skrócenia całego wymaganego przebiegu.
   W razie problemów przywrócić dotychczasowy układ, zachowując testy i dowody.

Przykład rachunku, nie pomiar: skrócenie krytycznej kontroli z 28 do 16 minut
przy 12 przyszłych sekwencyjnych odbiorach daje 2,4 h brutto. Przy 3 h kosztu
wdrożenia sam ten efekt nie uzasadnia zmiany. Wcześniej ustalić liczbę odbiorów,
rzeczywisty czas całego CI i możliwość wykonania wdrożenia poza ścieżką krytyczną.

Odbiór: pełne pokrycie dotychczasowych kontroli, poprawne odrzucanie awarii
i zmierzony dodatni zwrot czasowy. Pomiar obejmuje także kolejkę runnerów.

## P09. Opcjonalnie ograniczyć redundantne uruchomienia CI gałęzi

**Impact: 0–2 godziny netto, jeśli zmiana się zwróci. Koszt: 1–3 godziny.**
Najczęstsza korzyść to mniej runner-minut. Przy braku kolejki wpływ na termin
może wynieść 0 h; koszt wdrożenia może wtedy wydłużyć projekt. Wariant opcjonalny.

Właściciel: właściciel polityki CI i ochrony repozytorium.

Ważne: aktualne [zasady zmian](contributing.md),
[walidator](../scripts/check_repository.py) i
[workflow](../.github/workflows/required-ci.yml) wymagają push `main` oraz `ai/**`,
PR i ręcznego startu. Samo usunięcie triggera naruszy obowiązujący kontrakt.

Kolejność wykonania:

1. Zmierzyć, ile czasu zajmują przebiegi push i PR oraz oczekiwanie na runner.
   Rozróżnić branch HEAD, testowaną rewizję PR i późniejszy merge na main.
2. Wybrać jeden konkretny wariant: zachować obecną politykę albo zastąpić
   kontrolę push gałęzi z otwartym PR wymaganym odbiorem PR. Określić również
   zachowanie gałęzi bez PR i PR otwartego dopiero po wcześniejszym push.
3. Opisać zmianę polityki i jej uzasadnienie w osobnym PR. Spójnie zmienić
   workflow, walidator, testy, dokumentację oraz mapę wymaganych statusów.
   Nie uzyskiwać zielonego wyniku przez ogólne usunięcie walidacji.
4. Zachować pełny odbiór PR oraz późniejszego push na main, kontrolę aktualności
   gałęzi i niezbędne bramki. Ręczny start nie zastępuje odbioru PR.
5. Sprawdzić otwarcie, aktualizację i ponowne otwarcie PR, wyścig push/PR,
   ręczny start, awarię i anulowanie. Wymagany status nie może pozostać
   nieoczekiwanie pending ani uzyskać sukcesu z pominiętych kontroli.
6. Sprawdzić uprawnienia rozwiązania. Nie poszerzać dostępu do sekretów lub
   kodu z niezaufanych PR tylko w celu wykrywania duplikatu workflow.
7. Po przeglądzie i wymaganym odbiorze zmierzyć oszczędność. Zachować możliwość
   przywrócenia poprzedniego workflow i kontraktu jako jednej zgodnej zmiany.

Odbiór: polityka i egzekwujące ją mechanizmy są zgodne, wszystkie wymagane
rewizje nadal są testowane, a oszczędność uzasadnia koszt.
Domyślna decyzja przy braku takiego dowodu: pozostawić dotychczasowe triggery.

## P10. Ograniczyć eksperymenty i wykorzystać istniejącą implementację

**Impact: 2–6 godzin netto. Koszt: 0,5–1 godziny planowania.**
Przy braku nadmiarowego strojenia lub powielania kodu dodatkowa korzyść może
być zerowa. Zakres obowiązkowych porównań pozostaje pełny.

Właściciele: AI 07, AI 08, AI 09 i AI 10 w swoich obszarach.

Kolejność wykonania:

1. Zamrozić przed końcową oceną listę modeli, przestrzeń konfiguracji,
   budżet prób, kryterium wyboru i regułę zakończenia strojenia.
2. W AI 07 porównać wymagany seasonal-residual baseline z Isolation Forest.
   W AI 08 porównać LogisticRegression i HGB, kalibrację oraz wymagane warianty
   cech, w tym forecast i sprzedaż ograniczoną dostępnością.
3. W AI 09 użyć kompaktowego Keras. Zacząć od jednej architektury i niewielkiego
   budżetu development uzasadnionego pomiarem; ewentualne rozszerzenie ma decyzję
   i koszt, zamiast nieograniczonej pętli kolejnych konfiguracji.
4. Pozostawić prostszy model championem, jeśli tak wynika z pełnej poprawnej
   oceny. Challenger musi być rzeczywiście wykonany, zapisany i porównany.
5. Wykorzystać lifecycle, registry, batch, auth i formaty błędów AI 05 przez
   wąskie adaptery. Unikać przebudowy całego frameworka w trakcie domykania etapów.
6. W AI 10 rozbudować istniejący runner i frontend. Na podstawie mapy braków
   implementować potrzebne projekcje, eksport, inbox/outbox i scenariusze awarii.
7. Pozostawić opcje poza AI 00–10 w backlogu późniejszych etapów. Rzeczywisty
   agent, jego sugestie, pełny Kubernetes/GitOps i pokaz AWS mają własne odbiory.

Odbiór: wszystkie wymagane modele i warianty oceniono, budżet jest przestrzegany,
decyzje jakości zachowują rzeczywiste wyniki, a ponownie użyte moduły mają
testy właściwe dla nowego zastosowania. Pomiar: liczba prób względem budżetu
oraz godziny unikniętej duplikacji implementacji.

## P11. Zaplanować zasoby i warunkowo użyć osobnego workera

**Impact: 0–6 godzin netto. Koszt: 0,5–1 godziny planowania; 1–4 godziny
dodatkowo przy konfiguracji zdalnego workera.**
To warunkowy potencjał; do planu bazowego nie wpisywać dodatniego impactu
workera przed pomiarem. Zysk nakłada się na P03, P04 i P08.

Właściciel: koordynator zasobów, będący jednym z właścicieli strumieni.

Kolejność wykonania:

1. Odczytać bieżące wolne miejsce, pamięć, obciążenie i aktywne procesy.
   Ustalić wymagany zapas dysku, uwzględniając wcześniejsze ustalenia użytkownika.
2. Przygotować budżet każdego ciężkiego zadania: CPU, RAM, input/output bytes,
   czas i miejsce na bezpieczne wznowienie. Koszt dużych kopii uwzględnić jawnie.
3. Do czasu benchmarku wykonywać lokalnie najwyżej jedno ciężkie zadanie danych
   lub treningu naraz; pozostałe sesje mogą pisać kod i wykonywać lekkie kontrole.
   Zwiększać współbieżność dopiero po pomiarze realnej przepustowości.
4. Dla obliczeń wielowątkowych ustalić limity wątków zgodne z przydziałem CPU,
   aby każdy równoległy proces nie próbował używać całej maszyny.
5. Wykorzystywać istniejące niezmienne artefakty przez referencje, z osobnymi
   katalogami wyjściowymi. Nie usuwać danych aktywnych sesji ani materiału rollback.
6. Jeśli obliczenia ograniczają termin, porównać lokalny czas z pilotem jednego
   zadania na izolowanym Linux workerze. Uwzględnić transfer, instalację,
   przechowywanie wyników, kolejkę i ponowną weryfikację po pobraniu.
7. Przed płatnym lub zewnętrznym wykonaniem ustalić budżet, dopuszczone dane
   i dostęp. Używać przypiętych commitów i zależności; istniejących workflow
   AI 04 nie uruchamiać jako nowej kampanii bez właściwej decyzji i protokołu.
8. Po udanym pilocie rozdzielić niezależne seedy lub partycje z checkpointami.
   Zweryfikować kompletność i integralność scalonego wyniku, bez utraty dowodów.

Odbiór: zmierzony dodatni wpływ na termin po odjęciu całej obsługi workera;
zasoby mieszczą się w budżecie i nie destabilizują sesji lokalnych.
Sama liczba równoległych sesji lub rdzeni nie jest dowodem przyspieszenia.

## P12. Wcześnie integrować wyniki i domknąć jeden spójny odbiór

**Impact: 1–3 godziny netto. Koszt organizacji: 0,5–1 godziny.**
Końcowe testy i przeglądy należą do zwykłego zakresu projektu. Oszczędność
wynika z wcześniejszego wykrywania niezgodności i mniejszej liczby powtórzeń.

Właściciel: koordynator integracji wyznaczony w P01.

Kolejność wykonania:

1. Przed końcowym treningiem uzgodnić przykłady outputów i wykonać ich odczyt
   przez projektor/API/UI. Każda niezgodność wraca do właściciela kontraktu.
2. Ustalić kolejność scalania PR. Po zmianie wspólnej bazy aktualizować tylko
   zależne strumienie i ponawiać wymagane kontrole właściwej rewizji.
3. Po AI 07–08 przypiąć odebrane modele dla AI 10. AI 09 prowadzi własne
   porównania; późniejsza zmiana championa wymaga kontrolowanej aktualizacji
   kompatybilnego pakietu i ponowienia dotkniętej części odbioru.
4. Przygotować macierz końcową: wymaganie → dowód → commit → test → właściciel.
   Wcześniejsze dowody AI 00–06 zachowują zakres; nowe integracje mają nowe dowody.
5. Wykonać pełne E2E trzech rzeczywistych wyników ML i wymagane testy
   snapshot/replay, awarii, idempotencji, świeżości i uprawnień.
6. Potwierdzić pełną ocenę AI 09, artefakt TensorFlow, reload, koszty, segmenty,
   lineage i jawne decyzje modeli, również w razie przegranej challengera.
7. Zamknąć dokumentację i wymagane PR/CI. Scalenia wykonywać w granicach
   faktycznie udzielonych uprawnień. Odbiór chronionego main dotyczy jego rewizji.
8. Ogłosić AI 00–10 zakończone dopiero po sprawdzeniu całej macierzy. Samo
   ukończenie kodowania, przejście smoke lub zielony PR nie zastępuje pełnego DoD.

Odbiór: wszystkie wymagania AI 00–10 mają właściwe dowody bez luk i sprzecznych
wersji. Pomiar: czas końcowych poprawek kontraktów, powtórzeń CI i oczekiwania
na brakujące materiały w porównaniu z planem sprzed integracji.

## Harmonogram wdrożenia usprawnień

Podane okna dotyczą organizacji pracy i uruchomienia usprawnień, nie gwarantują
ukończenia funkcji w tych godzinach. Aktualizować je po P05 i pomiarze CI.

| Od rozpoczęcia wdrażania planu | Działania | Kontrola postępu |
|---|---|---|
| 0–1 h | P01, P02; ustalenie zasobów P11 | Aktualne SHA, właściciele, granice uprawnień i następne zadania |
| 1–3 h | Start P03 i P04, benchmark P05, projekt indeksu artefaktów P06 | Czy praca AI 09/10 faktycznie może iść bez czekania na modele? |
| 3–6 h | Decyzja o skali danych, pomiar CI, wybór opłacalnych P08/P09 | Nowe ETA, koszt/zwrot usprawnień, ewentualna korekta zakresu technicznego |
| Dalej do AI 07–08 | P03/P04 trwają równolegle; P06/P07/P10 obowiązują w każdym strumieniu | Gotowe kontrakty i adaptery, postęp modeli, stabilne rewizje |
| Po AI 07–08 | Kampania AI 09 oraz rzeczywista integracja AI 10 | Pełne dane, przypięte modele, brak konfliktu zasobów |
| Końcowy odbiór | P12, wymagane PR/CI i sprawdzenie macierzy | Komplet dowodów AI 00–10 |

Przykładowy podział czterech strumieni:

| Strumień | Podstawowa odpowiedzialność | Dodatkowa praca w naturalnym oknie oczekiwania |
|---|---|---|
| A | AI 07 | Uzgodnienie anomaly output i danych DQ z C |
| B | AI 08 i P05 | Uzgodnienie stockout output oraz artefaktów z D |
| C | AI 10 | Koordynacja kontraktów i końcowej integracji |
| D | Przygotowanie i wykonanie AI 09 | Pomiar CI; tylko opłacalne, ograniczone P08/P09 |

Właściciel D wykonuje te zadania kolejno, nie jest dwoma zasobami naraz.
Jeżeli zmiana CI opóźni Keras/evaluator będące na ścieżce krytycznej, odłożyć ją.

## Jak policzyć łączny impact bez zawyżania

Po pierwszym benchmarku i następnie przy ukończeniu większego przyrostu
aktualizować graf zależności i przewidywany czas najdłuższej ścieżki.
Koordynator zapisuje trzy rodzaje korzyści osobno:

| Rodzaj | Sposób rozliczenia |
|---|---|
| Skrócenie terminu | Różnica dat zakończenia najdłuższej ścieżki przed/po zmianie |
| Oszczędność zasobów | Runner-minuty, CPU-godziny, RAM/dysk i transfer; bez automatycznego przeliczenia na termin |
| Uniknięte ryzyko | Osobny scenariusz opóźnienia, np. późna przebudowa danych; bez doliczania do pewnej oszczędności |

Grupy silnie nakładających się efektów:

- P03 + P04 + P11: wcześniejszy start i dostępność zasobów.
- P02 + P07 + P08 + P09: postoje, pełne przebiegi i czas oczekiwania na CI.
- P01 + P06 + P10 + P12: spójność kontraktów, ponowne użycie i mniej poprawek.
- P05: wykonalność skali i ryzyko późnego zatrzymania kilku strumieni.

Przykładowe spójne scenariusze całego pakietu, uwzględniające koszty wdrożenia
i nakładanie korzyści; są to cele do weryfikacji, nie wyniki benchmarku:

| Scenariusz | Bez reorganizacji | Po usprawnieniach | Łączny impact netto |
|---|---:|---:|---:|
| Sprawne przejście wymaganych bramek | 42 h | 30 h | 12 h, około 29% |
| Centralny plan roboczy | 57 h | 39 h | 18 h, około 32% |
| Wolniejsze kontrole i integracja w przyjętym zakresie | 72 h | 48 h | 24 h, około 33% |

Duża usterka danych, brak jakości wymaganej przez protokół lub długotrwała
niedostępność środowiska wymaga nowego szacunku i może przekroczyć tę tabelę.
Pominięcie wymaganych porównań, TensorFlow, bramek jakości lub trwałości
transportu zmieniłoby zakres AI 00–10 i nie jest sposobem zaliczenia tego planu.

Rejestr pomiarów do uzupełniania podczas wykonania:

| Pole | Co zapisać |
|---|---|
| `improvement_id` | P01–P12 |
| `baseline_commit` / `candidate_commit` | Dokładne rewizje dla porównania |
| `setup_hours` | Rzeczywisty koszt wdrożenia |
| `elapsed_before` / `elapsed_after` | Porównywalny czas przebiegu lub zadania |
| `remaining_repetitions` | Liczba powtórzeń, które pozostały do zakończenia |
| `critical_path_before` / `critical_path_after` | Aktualne przewidywane zakończenie całego projektu |
| `resource_saving` | Osobno oszczędność zasobów |
| `overlap_with` | Inne kroki wykorzystujące tę samą korzyść |
| `decision` | Wdrożyć, pozostawić obecną wersję albo wycofać optymalizację |

Jeżeli koszt optymalizacji na ścieżce krytycznej przewyższa oczekiwaną korzyść,
zakończyć ją na pomiarze i wrócić do zadań wymaganych przez DoD.

## Źródła i odtworzenie podstaw szacunku

Obowiązujące lokalne zasady: [contributing](contributing.md),
[polecenia i kontrole](development.md),
[walidator dokumentacji/CI](../scripts/check_repository.py),
[workflow Required CI](../.github/workflows/required-ci.yml).
Wykonawca planu musi sprawdzić ich wersję na swojej aktualnej gałęzi.

Materiały odczytane w repozytorium RetailOps, na lokalnej rewizji
`599735a5fd13328e05f6100a07f644f4676e1ced`:

- [Mapa zależności i repozytoriów](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/599735a5fd13328e05f6100a07f644f4676e1ced/docs/plans/ai/kolejnosc-i-repozytoria.md).
- [AI 07 — anomalie i DQ](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/599735a5fd13328e05f6100a07f644f4676e1ced/docs/plans/ai/etapy/07-anomalie-dq.md).
- [AI 08 — stockout](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/599735a5fd13328e05f6100a07f644f4676e1ced/docs/plans/ai/etapy/08-stockout-risk.md).
- [AI 09 — TensorFlow i robustness](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/599735a5fd13328e05f6100a07f644f4676e1ced/docs/plans/ai/etapy/09-tensorflow-robustness.md).
- [AI 10 — integracja](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/599735a5fd13328e05f6100a07f644f4676e1ced/docs/plans/ai/etapy/10-integracja-retailops.md).
- [Profile i bramki](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/599735a5fd13328e05f6100a07f644f4676e1ced/docs/plans/ai/kontrakty/profile-i-bramki.md).
- [OPS-03 — trwałość przetwarzania](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/599735a5fd13328e05f6100a07f644f4676e1ced/docs/evidence/ops/03/README.md).

Materiały AI 08 odczytane z worktree `/private/tmp/retailops-ai08-upstream`,
którego HEAD przy zapisie instrukcji wynosił
`aa6b9537271f9d13666bccb8ff76ec99bfa103f0`:

- `docs/evidence/08-02-stockout-features.md` — pomiar 1632 punktów i 10 249 555 bajtów.
- `src/retailops_ai/stockout/feature_dataset.py` — limity cech.
- `src/retailops_ai/stockout/dataset.py` — limity wejść i etykiet.
- `scripts/check_repository.py`, `tests/test_ci_contract.py` i
  `.github/workflows/required-ci.yml` — egzekwowany kontrakt CI.
- `/private/tmp/ai08-upstream-ci-local.log` — 1816 passed w 1678,21 s.

Identyfikatory sesji wykorzystanych wyłącznie do odczytu postępu:

- AI 07: `01a0fb9d-d3a9-7dc1-b076-c02c96a12007`.
- AI 08: `01a0f1f1-396b-7ef1-8b59-7804218ef80f`.

Ścieżki `/private/tmp` i zapisy sesji są lokalnymi źródłami pomocniczymi,
nie trwałym kontraktem repozytorium. Przed wdrożeniem zebrać świeże dowody
z istniejących commitów, raportów i bieżących wyników CI.


## Aktualizacja CI — 2026-10-05, AI10 #25

To uzupełnienie P07–P08. Nie zastępuje historycznej bazy 57 godzin ani nie
przeszacowuje całego pozostałego AI00–10. [Bieżący postęp](ai10-progress.md)
i [receipt](ai10-durable-observation-replay-ci-receipt.json) wiążą dokładne SHA.

### Fakty z tego odbioru

Na jednym commicie `571b174018937dad7b5b3b14ba44b4d6f4397c76`:

- Push Required CI `37272699953`: 5/5 success, broad 1874 passed / 31 skipped
  w 1438,68 s, czyli 23 min 59 s. Wszystkie 31 Docker-only cases wykonano
  osobno: 30 observation SQL i 1 outbox; 1905 różnych executed tests passed.
- PR attempt 1 `37272703816`: ten sam broad przeszedł w 2325,98 s, czyli
  38 min 46 s. Limit checks 45 min przerwał ostatni offline replay check,
  po zaliczeniu kontraktów i package. PR nie był w pełni odebrany.
- Pełne persistence PR: 31 min 09 s, success. Nowy osobny mandatory observation
  job: 30 passed / zero skipped, 23,03 s samego pytest i około 39 s całego jobu.
- Ponowiono tylko cancelled checks i aggregate, bez zmiany commitu, limitów
  lub zaliczonych bramek. Retry zakończył się sukcesem: PR attempt 2 ma 5/5
  success, broad 1874 passed / zero failed / 31 covered skips w 1397,55 s
  (23 min 18 s); czas całej powtórzonej próby 26 min 51 s. Wcześniej zaliczone
  persistence/secrets/observation zachowały wynik i nie były wykonywane ponownie.

Interwały między liniami modułów w logu pytest wskazują największe odcinki:
`test_v12_queue.py` około 415 s push / 588 s PR, `test_curated.py` 147 / 299 s,
`test_forecast_runtime.py` 93 / 170 s. To przybliżenie obejmujące logowanie
i fixture/setup, **nie profil CPU pojedynczych testów**. Zmienność runnera
uniemożliwia przypisanie różnicy do modelu Codex albo nowego kodu replay.

### Kolejność usprawnień i impact całego AI00–10

**CI-A: szybkie mandatory gates przed kosztownym odbiorem — część wykonana.**
Impact netto od teraz: **0–1 h całego projektu**, warunkowo przy kolejnych
błędach kontraktu lub storage. Na udanym przebiegu impact kalendarzowy może
wynieść **0 h**, ponieważ decyduje najdłuższy job. Nie sumować tego z P07/P08.

1. Zachować istniejący wczesny guard workflow i osobny job observation SQL.
   Nowe kontrole muszą być obowiązkowe w `required-result`.
2. Dla kolejnych kosztownych przyrostów najpierw uruchamiać minimalny test
   właściwego kontraktu i rzeczywistego storage. Pokrywa on ryzykowne zachowanie,
   a pełna regresja pozostaje wymagana.
3. Reagować od razu na failure tego szybkiego gate. W tym odbiorze oczekiwana
   nazwa powodu kwarantanny została skorygowana po 22,55 s pytest; produkcyjny
   zapis był poprawny. Szybki gate ograniczył czas wykrycia do około minuty.
4. W razie timeoutu odczytać dokładne logi i ponowić wyłącznie właściwy
   niedokończony job na tym samym SHA. Nie oznaczać cancelled run jako sukcesu.

**CI-B: rozdzielenie pełnego pytest od pozostałych checks i dwóch grup testów.**
Impact netto od teraz: **0–2 h całego AI00–10**, do potwierdzenia rachunkiem
liczby pozostałych odbiorów; koszt wdrożenia z odbiorem **1–3 h**. Jeśli zostały
tylko 1–3 pełne rundy albo projekt czeka głównie na model/zgodę, impact może
wynieść **0 h** i zmiana powinna poczekać. Zysk pojedynczego krytycznego workflow
szacujemy na **0–14 min** przy obecnych pomiarach i dominującym persistence
około 31 min; dodatkowy zysk może pochodzić z uniknięcia timeout/retry.

1. W najbliższym wymaganym pełnym przebiegu dodać `--durations=30`; nie uruchamiać
   dużej regresji wyłącznie po pomiar. Zapisać czasy wszystkich mandatory jobs.
2. W osobnym worktree zaprojektować dwa joby pytest: kolejka v12 wraz z wybranymi
   ciężkimi modułami oraz pozostała regresja. Wagi ustalić z faktycznych duration,
   nie z liczby plików. Osobne runners zachowują izolację portów/DB/katalogów.
3. Dodać target wszystkich pozostałych checks bez pytest. Lokalny `make check`
   nadal wykonuje komplet. CI musi wykonać niezmieniony łączny zakres testów,
   kontraktów, package, byte pins, quality acceptance i dokumentacji.
4. Porównać `pytest --collect-only` pełnego zestawu i obu grup: unia node IDs
   musi być identyczna, bez luk. Wspólne importy helperów nie mogą dodawać
   niejawnego podwójnego wykonania testów.
5. Zmienić razem workflow, Makefile, walidator i negatywne testy ochrony CI.
   Aggregate wymaga obu grup, pozostałych checks, secrets, rzeczywistego
   persistence oraz observation SQL. Failure/cancel/skip dowolnego required
   jobu blokuje odbiór; wyzwalanie PR/push pozostaje pełne.
6. Odbierać zmianę dopiero po pełnym CI i potwierdzeniu skrócenia critical path.
   Koszt wdrożenia odjąć od `liczba pozostałych krytycznych rund × oszczędność
   rundy + uniknięte retry`. Gdy wynik jest niedodatni, nie wdrażać jej dla
   przyspieszenia bieżącego projektu.

Podpunkty każdego CI-A/CI-B dzielą **jeden wspólny impact**; nie są dodatkowymi
oszczędnościami do zsumowania. Podział CI-B jest propozycją, nie został wykonany.


## Aktualizacja AI10 #26 — 2026-10-05

Osobny mandatory actual broker gate jest wdrożony i przeszedł 12/12 testów
PostgreSQL + Redpanda TLS/SCRAM: 19,99 s PR / 21,75 s push. Stara bramka SQL
30/30 i secrets są zielone; pełny Required CI PR i push zakończył się **6/6 success**,
oba attempt 1 na finalnym headzie, bez retry.
Przy pierwszym rzeczywistym odbiorze błąd event ID fixture wykryto po 20,68 s
pytest i poprawiono bez zmiany kodu produkcyjnego. To realizacja CI-A, z tym
samym wspólnym impactem **0–1 h netto całego AI00–10**, bez dodawania kolejnej
oszczędności. Na poprawnym przebiegu krótki gate nie skraca najdłuższego jobu.

Krok CI-B.1 jest wdrożony: najbliższy wymagany pełny pytest rejestruje
`--durations=30`. **Impact tego pomiaru: 0 h bezpośrednio**, koszt około kilku
minut implementacji i odczytu logu; dane mają pozwolić podjąć decyzję o
podziale. Sam podział CI-B pozostaje niewdrożony. Nie uruchomiono dodatkowej
długiej regresji tylko dla profilowania.

### Końcowy pomiar CI-B.1 z wymaganego odbioru #26

| Pomiar | PR | Push |
| --- | ---: | ---: |
| Cały Required CI | 36 min 10 s | 43 min 31 s |
| Mandatory checks | 35 min 24 s | 42 min 57 s |
| Mandatory persistence | 30 min 31 s | 30 min 25 s |
| Pełny broad pytest | 30 min 28,85 s | 37 min 8,18 s |
| Teoretyczny limit zysku przez skrócenie checks do poziomu persistence | 4 min 53 s | 12 min 32 s |

Ostatni wiersz jest **wnioskiem z czasu jobów**, warunkowym przy niezmienionej
kolejce/concurrency i pozostałych bramkach. To nie zmierzona oszczędność ani
obietnica wyniku sharding. Limit nie obejmuje ewentualnego skrócenia persistence.
Wszystkie sześć required jobs wykonano w obu przebiegach. Wynik odbioru:
1969 różnych testów passed; 43 skips broad pokryte rzeczywistymi osobnymi gates.
[Receipt z dokładnymi czasami i SHA](ai10-observation-broker-ci-receipt.json).

Najdłuższe zmierzone fazy call PR/push: `v12_queue` byte budget
129,31/165,57 s, maximum scope 80,97/101,48 s, full split 36,23/45,35 s;
curated concurrent 36,01/44,75 s; forecast backtest 33,78/41,17 s;
curated SIGKILL 29,77/36,31 s. To najwolniejsze 30 faz, nie pełne wagi modułów.
Przed podziałem z kroku CI-B.2 zebrać komplet potrzebnych wag i sprawdzić
identyczną unię node IDs. Nie usunięto testów ani nie zwiększono timeoutów.

**Impact wykonanego CI-B.1: nadal 0 h bezpośrednio.** Proponowany CI-B zachowuje
wspólny forecast **0–14 min na krytyczną rundę / 0–2 h netto całego AI00–10**
warunkowo po odjęciu **1–3 h** wdrożenia i odbioru. Powyższe dwa pomiary nie są
oszczędnościami do zsumowania i nie uzasadniają automatycznego wdrożenia.

### Przed połączeniem finalnych modeli z AI10

Odczyt owner AI08 `6fb5c7ae3b01e1e63deefe6acdc0fcdc4391068d` potwierdza
zakończone dane/jakość/lifecycle/serving; formalny PR/main CI nadal jest warunkiem
ready. Jego DB head to `0021_stockout_jobs`, a przyrost AI10 ma
`0021_observation_replay`. Potrzebna jest jawna integracja grafu migracji.
To aktualizacja kontroli P05/P12, nie zgoda na merge, promocję ani zmianę sesji.

**Impact zapobieżenia ponownemu odbiorowi migracji: 0–1 h całego AI00–10**,
warunkowo przy jednej unikniętej nieudanej rundzie CI; koszt audytu i merge
revision szacowany **0,5–1,5 h** należy odjąć od korzyści. Nie sumować z
P05/P12 ani CI-A, jeśli dotyczą tej samej unikniętej rundy.

1. Przypiąć zatwierdzone publiczne heady właścicieli, ich kontrakty i receipts;
   do czasu publikacji AI07 nie zastępować go domyślnym fixture i nie kopiować
   prywatnych pakietów do GitHuba. Read-only status nie uprawnia do ready.
2. W osobnym worktree AI10 odczytać `alembic heads/history` wszystkich integrowanych
   gałęzi. Sprawdzić nowe tabele, revision/down_revision oraz EXPECTED_REVISION.
3. Zachować opublikowane historyczne revision IDs. Dodać jawny merge revision
   lub kolejną addytywną migrację wynikającą z rzeczywistego grafu; nie zmieniać
   zatwierdzonych migracji i manifestów modeli tylko dla wyrównania numerów.
4. Na własnym PostgreSQL przejść upgrade z każdej wcześniej odebranej podstawy.
   Sprawdzić, że dane modeli, outbox i obserwacji pozostają zachowane, readiness
   wymaga właściwego pojedynczego final headu, a worker nie migruje przy startup.
5. Odebrać pełny backup/restore niepustych tabel wszystkich modeli i outbox oraz
   replay obserwacji. Zachować stare required gates, scope i oryginalne decyzje
   jakości; końcowy CI musi dotyczyć dokładnego zintegrowanego commitu.
6. Dopiero wtedy wiązać qualified output → outbox → Source projectors/API/UI
   i końcowe 102-day E2E. Sam green runtime fixture nie zamyka tego zakresu.

Wszystkie podpunkty dzielą wspólny impact; nie są sześcioma osobnymi zyskami.


## Aktualizacja AI10 Source #98 — 2026-10-05

Przyrost producenta obserwacji Source wdraża atomic version + outbox oraz
bounded TLS/SCRAM publication. Schemat przypięto do odebranego AI #26.
W API CI zachowano stare required gates i dodano 12 actual SQL/broker cases
do kroku durability przed broad pytest, z coverage i obowiązkowym JSON/JUnit.
Required CI dla finalnego headu `767d34273440fd86306768e96edaf24afcc4bdac`
zakończył się **29/29 success**; [receipt](ai10-source-observation-outbox-ci-receipt.json).

**Impact wczesnego gate: wspólny CI-A 0–1 h netto całego AI00–10**, warunkowo
przy uniknięciu późno wykrytego błędu. To nie kolejna godzina do dodania do P07/P08
i poprzednich aktualizacji. Na poprawnym przebiegu koszt krótkiego testu może
nie skrócić critical path. Końcowy pomiar podano poniżej; nie przypisujemy potencjalnej oszczędności jako zmierzonego skrócenia projektu.

1. Uruchomić nowe real durability cases w istniejącym obowiązkowym wczesnym kroku,
   bez usuwania starych kontroli. **Wdrożone; podpunkt dzieli wspólny impact CI-A.**
2. Przy failure odczytać dokładny test/SQL/broker boundary i naprawić przyczynę;
   finalną akceptację wiązać z nowym headem i kompletem required jobs. **Impact
   tego podpunktu mieści się w CI-A; nie dodawać oddzielnie unikniętej rundy.**
3. Gdy wszystkie jobs przejdą, zapisać runtime/JUnit/byte hashes i dopiero
   przejść do pełnego Source capture albo zatwierdzonych modeli. **Impact samego
   zapisu odbioru: 0 h bezpośrednio**; ogranicza powtórne sprawdzanie stanu.
4. Zachować receipts publishera jako dowody delivery; nie używać ich jako pełnego
   snapshot barrier. Crash przed SQL completion może pozostawić nieudokumentowany
   wcześniejszy offset. **Impact uniknięcia fałszywego handoff: 0–1 h całego
   projektu warunkowo**, w ramach kontroli P05/P12 i tego samego CI-A, jeśli
   chodzi o tę samą unikniętą nieudaną rundę. Implementacja pełnego capture 43 tabel
   pozostaje osobną pracą i nie została zaliczona jako oszczędność.

Nie zmieniono numerical/runtime requirements ani nie uruchomiono lokalnego
Dockera. CI-B sharding i integracja grafu finalnych migracji AI07/08 pozostają
otwarte; sama liniowa migracja Source nie zastępuje tej integracji.


### Końcowy pomiar CI-A dla Source #98

Finalny Required CI: **31 min 22 s** od utworzenia do końcowego success,
w tym **9 min 1 s** do startu pierwszego joba; wykonanie jobów zajęło
przedział **22 min 19 s**. API job: **15 min 14 s**, broad pytest **11 min 54,72 s**,
obowiązkowy early durability **88,48 s** (58 różnych cases, w tym 12 nowych).
Suma czasów JUnit samych 12 nowych cases to **15,955 s**; nie obejmuje wspólnego
setup/teardown, więc nie traktować jej jako pełnego kosztu bramki.

Pierwsza nieudana próba wykryła problem serializacji prywatnego fixture SCRAM
w early step po 80,67 s. Następna kontrola diff wykryła blank EOF; oba błędy
poprawiono, a finalne 29 jobs odebrano na nowym headzie. To dowód działania
wczesnych kontroli, nie pomiar unikniętej późnej rundy. Odczyt queued podczas
odbioru był opóźniony; finalne czasy pochodzą z completed job timestamps.
Przyczyna opóźnienia przydziału nieustalona; nie zmieniano concurrency ani runów
innych sesji. Samo skrócenie testów nie usuwa czasu przydziału runnerów.

**Impact wykonanego pomiaru i receipt: 0 h bezpośrednio.** Warunkowa prognoza
CI-A pozostaje **0–1 h netto całego AI00–10** dla tej samej unikniętej późnej
nieudanej rundy. Nie dodawać do siebie wcześniejszych aktualizacji CI-A ani
pokrywających się P05/P07/P08/P12. Sharding CI-B i pełny capture pozostają
osobnymi krokami; green fixture producenta nie zamyka całego AI10.


## Usprawnienia końcowego odbioru AI10 — 2026-10-07

Impact dotyczy czasu od teraz do AI00–10. Nie sumować z wcześniejszymi
prognozami ani ze wspólnymi zmianami CI #34. Implementacja lokalna przeszła
kontrole; odbiór zdalny i chroniona publikacja pozostają wymagane.

1. **Wczesny preflight rzeczywistej aplikacji v12.** Po locked install wywołać
   `preflight_application_database`, przed archive/Source. Sprawdza actual
   Settings/application z własnym `ai_app/retailops_ai`; potem wykonać wszystkie
   niezmienione native gates. **Impact projektu: 0 min dla poprawnej konfiguracji;
   warunkowo 40–65 min przy uniknięciu jednej podobnej późnej awarii.** Poprzedni
   runtime zatrzymał się dopiero po pełnym MLflow import i preload. Jest to
   ograniczenie ryzyka, nie zmierzona oszczędność poprawnego przebiegu.
2. **Uruchomić pełny native v12 bez osobnego prerequisite archive-only.**
   Przygotować świeży własny GET-only handoff, exact SHA mapy i closed inference
   date. Native sam odzyskuje i weryfikuje wszystkie 669 plików. Archive-only
   wykonać tylko jako potrzebną diagnostykę. **Impact projektu: 20–23 min, gdy
   inaczej oba odbiory byłyby wykonane szeregowo; aktualnie 0 min**, ponieważ
   już używamy direct native. Nie ma persistent/public cache archiwum; pełny
   verifier i cold/recovery pozostają wykonywane.
3. **Wybrać dotkniętą ścieżkę stockout/anomaly.** Manual: lane `all`, `stockout`
   albo `anomaly`. Automat korzysta z exact git diff; tylko znane pliki acceptora
   mogą pominąć drugi model. Brak zakresu, common, unknown albo zmiana workflow
   uruchamia oba. Zachować wszystkie gates wybranej ścieżki i pełny Required CI.
   **Impact projektu: 0–5 min na przyszłą rundę**, zależnie od runner contention.
   Parallel native modele zwykle dają 0 min na critical path. Uniknięta ponowna
   kwalifikacja stockout oszczędza orientacyjnie 5–7 min runnera; nie oznacza
   automatycznie takiego skrócenia całego projektu.
4. **Zachować exact receipts odbiorów.** Wiązać każdą próbę z HEAD/Source/ZIP SHA
   i nie zastępować finalnego pass wcześniejszym partial pass. Anomaly ma 1232
   original SQL ACK i 25 stron UI, stockout 40 ACK i pełny UI. V12 failed receipt
   zachowuje wykonane wcześniejsze gates. **Impact bezpośredni: 0 min**;
   uniknięcie zbędnych powtórzeń mieści się we wcześniejszym P07.

Reuse wyników semantycznego verifiera wewnątrz procesu nie wdrożono i nie
przypisano mu impactu. Original recovery już pobiera niezależne pliki równolegle;
nie zaliczamy tej istniejącej właściwości jako nowego przyspieszenia.
