# Odświeżenie źródeł i etykiet RAG

Rejestr korpusu jest zamkniętym snapshotem dwóch lokalnych commitów Git.
Odświeżenie wybiera pełne SHA, odtwarza checksumy dokumentów, code/evidence refs,
fragmenty i nowego kandydata. Nie śledzi ruchomego HEAD podczas jednego builda.
Niezacommitowane treści i nieobecne na jawnej liście pliki pozostają poza korpusem.
[Odbiór](evidence/11-sources.md), [aktualny stan](STATUS.md).

## Metadane i zakres twierdzeń

Przegląd techniczny zachowuje konserwatywne statusy. Plany/polityki pozostają
`specified`; `implemented` wymaga pliku kodu z tej samej rewizji i ogranicza
fact scope do wskazanej implementacji. `verified` wskazuje wersjonowany wynik,
datę i commit pomiaru. Aktualna rewizja dokumentu może opisywać wcześniejszy
pomiar — nie staje się on nowym testem tej rewizji.

Instrukcje RAG opisują ścieżki offline/test; evidence potwierdza tylko ich jawny
zakres. Fake vectors, raport podobieństwa i techniczny sukces indeksowania
nie potwierdzają jakości semantycznej lub użytkowej aktywacji. Karta RF nadal
opisuje syntetyczną diagnostykę ze statusem rejected, bez prawa do serving.
Dokumentacja kontraktu seed RetailOps dotyczy legacy/v1; nie jest dowodem
zgodności nowego source 2.x z importerem AI.

Klasa `public_project` dotyczy jawnie wybranych, śledzonych dokumentów projektu,
bez prywatnych plików, uploadów, raw facts, truth i credential. Zmiana rejestru
nie nadaje nikomu grantu odczytu/indeksowania ani nie rozszerza istniejącego scope.

Kontrolowana walidacja code/evidence refs sprawdza ich obecność i checksumy,
nie przypisuje statusu na podstawie odpowiedzi LLM. Pole review_owner wskazuje
odpowiedzialność; `review_state=proposed` pozostaje do akceptacji korpusu i etykiet.
Techniczny przegląd metadanych nie tworzy CorpusApproval.

## Etykiety przed ewaluacją

Golden labels są autorstwa przeglądu sekcji, a nie wyników rankera. Po zmianie
źródeł należy ponownie wybrać expected/forbidden sections, statusy, scope i tools,
związać nowy index ID i golden set ID, zachowując progi przyjęte przed pomiarem.

Walidator sprawdza **obie** listy sekcji w pełnym kandydacie. Usunięta/zmieniona
sekcja zabroniona nie może dawać pozornie poprawnego wyniku przez brak dopasowania.
Duplikaty etykiet są odrzucane. Sekcja zabroniona może być poza filtrem/grantem
principal; jej binding jest sprawdzany przed retrieval, bez ujawniania tekstu.
Oczekiwana sekcja musi być dostępna w żądanym scope/status/filter.

Każde odświeżenie wymaga ponownej kontroli podobieństwa i prywatnej ewaluacji
fake kandydata. Nowy kandydat i raporty powstają w nowych plikach `0600`, bez
nadpisania poprzedniego indeksu. Niska jakość fake pozostaje wynikiem pomiaru,
bez zmiany etykiet/progów po zobaczeniu rankingu.

Użytkowy profil, końcowa akceptacja źródeł/etykiet i kwalifikacja golden są
oddzielnymi bramkami. Real embeddings, Bedrock i agent należą do etapu 12.
