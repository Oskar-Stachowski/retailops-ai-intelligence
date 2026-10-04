# AI 09.9 — pełny audytowany source → curated replay

Odbiór dotyczy [weryfikatora](../forecast-source-replay.md) na istniejącym,
publicznym smoke snapshot 1.0.0 i rzeczywistym curated. Sprawdza mechanikę
konsumenta; nie jest oceną jakości modelu ani nowych danych projektu.
Cały AI 09 pozostaje `in_progress / not_ready`.

## Weryfikacja funkcjonalna

Skupiona regresja: **226 passed w 202.82 s**, w tym **45 nowych testów**.
Obejmuje pełny typed replay, pięć trwałych rezerwacji przed I/O, częściowe
wyczerpanie budżetu bez odczytu, zmianę rodziców i runtime, przerwanie oraz
cleanup. Poprawnie ponownie zapieczętowane podmiany ilości, availability,
source lineage i tabeli poza targetami są odrzucone mimo przejścia istniejącego
`verify_curated`. Truth i unlisted files są odrzucane przed ich hash/copy.
Sprawdzenie typów: 355 source files bez błędów.

## Odbiór pakietu i zasobów

Wersjonowany [receipt](09-09-forecast-source-replay.json) zapisuje końcowe
wyniki native/wheel, pomiary całego własnego procesu, inventory i stan
dziennika. Pomiar obejmuje importy, rezerwacje, pełne odtworzenie i sprawdzenie
rodziców. Nie obejmuje wcześniejszego utworzenia znanego smoke fixture.
Role i ich klucze w tym odbiorze są deklaracjami metadanych; fizyczne pięć
populacji oraz scoped etykiety nie są kwalifikowane tym przyrostem.

Wszystkie wymagane lokalne komponenty są zaliczone: **2065 testów głównych
w 2093.60 s** oraz **3 rzeczywiste testy TensorFlow CPU w 85.57 s**, bez skipów,
lint/format, mypy, kontrakty, dane, pakiet i Compose config. Oryginalne
`make ci-local` ukończyło całe `make check`, po czym skan katalogu zgłosił
dwukrotnie ten sam kanoniczny SHA-256 planu dostępu. Pierwotny run z exit 2
i wall 2402.36 s jest zachowany. Przeliczono plan, potwierdzono digest i
dodano wyjątek do samej reguły `generic-api-key`, z AND dokładnej ścieżki oraz
dokładnego hasha. Kontrola negatywna wykrywa inne wartości w tym pliku i
ten sam hash poza nim. Globalna propozycja wyjątku była zbyt szeroka i została
odrzucona przed publikacją. Po zmianie tylko `.gitleaks.toml` oba skany
`make secrets` przeszły. Kod aplikacji, testy, schematy, pakiet i lockfile
pozostają identyczne, więc nie powtarzano całej regresji.
Granice reguły opisuje [oficjalna konfiguracja Gitleaks](https://github.com/gitleaks/gitleaks#configuration);
końcowy receipt zachowuje próbę oraz powtórzony skan.
Poprzedni commit `541dd7f4939c32303b4ebd577f70fd2dfbd7a758` ma
zielone [Required CI170](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37227245663).
To dowód poprzedniego przyrostu; nowy commit potrzebuje własnego CI.

Native i zainstalowany wheel odtwarzają **25 tabel / 31 171 wierszy**.
Wszystkie pola receipt poza unikalnymi access IDs są identyczne. Wszystkie
**280 plików Python** pakietu są identyczne z source; 45 zaimportowanych
modułów konsumenta pochodzi z installed wheel. Oba nowe schematy są
sprawdzone na rzeczywistych instancjach. TensorFlow i Keras nie są importowane.
Native trwał **10.14 s**, wheel **11.16 s**; peak całego własnego drzewa RSS
to **108.36 MiB**, próbkowany logiczny scratch **36 873 750 B (35.17 MiB)**.
Scratch to suma `stat().st_size`, nie liczba zaalokowanych bloków. Sampler
co 0.05 s egzekwował 1 GiB RSS, 512 MiB scratch, 120 s i 50 GiB wolnego dysku.
Najmniej wolnego dysku w poprawnie odebranych komendach: **52.97 GiB**.

Pierwszy pomiar wheel stracił końcówkę podczas usuwania scratch; reader
zakończył się poprawnie. Jego 5 rezerwacji i pierwszy native są zachowane.
Powtórny pomiar wykorzystał ostatnie 5 odczytów tego samego kontrolnego
journalu. Końcowy stan: **15 reserved / 15 completed / 0 failed / 0 unresolved**;
limit nie został podniesiony. Kolejne wywołanie jest odrzucone przed parent I/O,
a dziennik pozostaje identyczny. Niepełny pomiar nie jest użyty jako dowód zasobów.

## Granice i kolejny krok

Nie wykonano nowych fitów projektu ani otwarcia portfolio final test.
Wspólny dziennik projektu zachowuje historię, cztery istniejące plany i
64 dostępne odczyty. Kontrolny journal smoke ma osobny, jawny zakres;
nie zastępuje dziennika projektu i nie stanowi jego resetu.
Receipt nie jest zaufanym cache dla przyszłego eksportera.

Read-only sprawdzono AI 08.13 na `4faaf4b6c1997fda3a609645595165643bf302a9`:
pełny parent replay w prywatnym kontekście jest już użyty w stockout.
Nowy kod AI 09 reuse'uje istniejący importer, curated transform i forecast
bounded hashing; nie zmienia adapterów o innym grain ani otwartych sesji.
Odczytano też nowy receipt 08.14 na `12db74b0a98e51b0d9c0c9112f15824958b82d4b`:
pilot całego stockout ma 2856 origin i peak RSS 622.23 MiB. To inny profil
z generacją oraz sześcioma fitami; nie porównujemy go z kosztami samego
forecast source replay i nie ponawiamy tego pilota.
Profil 1.1 i większe dane pozostają poza tym odbiorem.

Pozostają audytowany eksport wersji do pełnych kluczy, jawna kwalifikacja
kompletności/jakości, integracja z zamrożonym treningiem, kalibracja i pełna
kampania po odbiorze odpowiednich zależności AI 07–08.
