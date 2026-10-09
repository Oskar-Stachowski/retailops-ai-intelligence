# Granica publicznej paczki

Pełny lokalny `make ci-local` po 5058 udanych testach i 49 opisanych
pominięciach zatrzymał się podczas budowy archiwum źródłowego. Backend
pakowania dołączył `.local` z tymczasowym testem dowiązania bezwzględnego,
a `uv` odmówił rozpakowania archiwum. Kontrole TensorFlow nie zostały osiągnięte.

Konfiguracja jawnie wyklucza prywatne katalogi niezależnie od rozpoznawania
reguł Git w worktree. Sdist zawiera źródła runtime, kontrakty, oba potrzebne
locki oraz metadane projektu. Prywatne dane, modele, raporty, cache i pliki
środowiska nie należą do tego zestawu. Testy, narzędzia repozytorium i docs
pozostają w Git oraz w pełnej kolekcji CI.

Rzeczywisty test buduje sdist i wheel przy obecności prywatnych plików,
pliku `.env` i dowiązania bezwzględnego. Sprawdza całe archiwum, następnie
buduje wheel z rozpakowanego sdist i porównuje bajt po bajcie wszystkie
pliki runtime z wheel zbudowanym bezpośrednio. Ta kontrola przeszła.
[Dowód 09.70](evidence/09-70-package-private-boundary.json) zachowuje pierwotną
awarię i zakres poprawki. Końcowy pełny CI oraz chroniona publikacja tej zmiany
pozostają wymagane.

Poprzedni PR #63 ma już pełny odbiór: 17 jobs dokładnego HEAD oraz 17 jobs
wynikowego main `12484dee4db4b044e61460bac14f1612321900a8`. Nie kwalifikuje
to nowych zmian ani pełnej kampanii AI 09; nie uruchomiono kolejnej diagnostyki.
