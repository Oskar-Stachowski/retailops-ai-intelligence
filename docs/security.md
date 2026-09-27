# Bezpieczeństwo

Repo udostępnia lokalny serwis diagnostyczny, bez publicznego wdrożenia.
[Granice HTTP](http-service.md): loopback, kontrola Host, token metryk,
walidowany kontekst i logi bez wartości wejściowych.
Nie deklaruje produkcyjnego auth, RBAC, szyfrowania danych ani gotowych modeli.
Każdy przyszły endpoint administracyjny wymaga granicy dostępu od pierwszej wersji;
`user_id` i demo-admin RetailOps nie są tożsamością dla AI.

Konfiguracja nie trafia do logów ani odpowiedzi diagnostycznych.
Wzory zawierają wyłącznie bezpieczne wartości. Nie zapisuj tokenów w argv,
Git, issue ani evidence. W razie ujawnienia unieważnij/obróć sekret; samo usunięcie
z najnowszego pliku nie usuwa go z historii.

Gitleaks skanuje aktualny katalog i historię. Wyjątki dotyczą tylko generowanych
venv/cache/build; nie ma allowlist dla źródeł. Workflow ma przypięte actions,
minimalne permissions oraz wyłączone komentarze i upload wykrytych sekretów.
Gitleaks Action v3 obsługuje osobiste repo bez license key; przeniesienie do
organizacji wymaga sprawdzenia warunków upstream przed uruchomieniem tej akcji.

Zgłoszenia omawiaj prywatnym, istniejącym kanałem z właścicielem projektu
(Oskar Stachowski). Nie ma jeszcze zweryfikowanego publicznego adresu ani
zdalnego Security Advisory. Nie twórz publicznego issue z detalami podatności.
Po opublikowaniu repo ustal dostępny kanał private vulnerability reporting
i aktualizuj ten dokument. Brak deklarowanego SLA odpowiedzi na obecnym etapie.
