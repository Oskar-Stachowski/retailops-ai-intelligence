# Odbiór zatwierdzonych profili i runów golden

Zakres: osobne zgody właściciela, profil związany z golden set i pełny raport
także po niezaliczonym progu. [Instrukcja](../knowledge-golden-jobs.md),
[zapis pomiarów](11-golden-jobs.json).

Właściciel w sesji 2026-09-28 udzielił zgód: „wykonaj kolejny krok, masz ode
mnie wszystkie zgody”. Decyzje obejmują przypięty korpus 29 dokumentów/
451 fragmentów i 44 pytania; wejścia i progi pozostają niezmienione.

Testy nowego zakresu: 16/16 w 8,16 s. Regresja administracji, kwalifikacji
i persistence: 91/91 w 35,44 s. Sprawdzono brakujące/niespójne zgody,
snapshoty i konfiguracje, kompletność decyzji podobieństwa, zachowanie
progów, fałszywe flagi/binding oraz prywatny eksport bez nadpisania.

Fake nie potwierdza jakości semantycznej. Użytkowa aktywacja, groundedness
odpowiedzi i wykonanie narzędzi agenta nie należą do tego odbioru.

## PostgreSQL i HTTP

Pełny lokalny Compose przechodzi na rewizji `0007_rag_golden_jobs`.
Syntetyczne profile golden dają `succeeded` i `failed`; oba raporty oraz
profile zachowują tożsamość po SIGKILL i down/up. Sprawdzono 401, local
POST/GET, terminalną idempotencję, granicę środowiska, retry workera, prywatny
eksport i brak aktywacji. SQL odrzuca sukces z podmienionymi flagami przy
niezaliczonej metryce; porównuje progi zapisane w profilu. Wcześniejszy zakres
workera zachowuje odbiór SIGKILL/wznowienia i anulowania.

## Rzeczywisty zatwierdzony korpus

Źródła profilu odtworzono z przypiętych Git commits przed rejestracją
w kontrolowanym kontenerze utrzymaniowym. Profil ma ID
`index-build-profile-sha256-89c6354a6a7339ba6836cccddb9976e586a2edd139b1a522d2cf5070efbd1424`.
Run `run-97f56aee10e7814998ffa55755106875` wykonuje wszystkie 44 pytania;
9/9 krytycznych kontroli przechodzi, Recall@5 i MRR wynoszą 0,0441176471.
Run ma `failed/gate_failed`, bez outputu, z pełnym raportem; nie zmieniono
etykiet/progów na podstawie wyniku. Po SIGKILL/restart PostgreSQL odczytano
ten sam run i report ID; lokalny current pozostaje pusty.

Profil, indeks i pełny raport są prywatnymi plikami `0600` poza Git.
Obie zgody i aktualna polityka podobieństwa są wersjonowane. Preflight z
oboma zgodami pozostawia trzy blokady: jakość golden, real provider i polityka
użytkowej kwalifikacji. Poprzedni pomiar pięciu blokad zachowuje własny wynik.

## Czysty odbiór i odtworzenie

Pełne `make bootstrap UV=.tools/bin/uv ci-local GITLEAKS=/opt/homebrew/bin/gitleaks`
w osobnym klonie na commicie `8df02e60df600e2771f4481d18b09517b0500d0e`
przechodzi: **627 testów w 257,62 s**, Ruff/format, strict Mypy
(92 pliki), linki, snapshoty kontraktów, wheel/sdist, Compose config oraz
Gitleaks git/dir. Venv utworzono od nowa dla tego odbioru; checkout pozostaje
czysty. Wyjątek Gitleaks dotyczy jednej konkretnej, zweryfikowanej checksumy
wygenerowanego OpenAPI — nie poświadczeń lub całej ścieżki evidence.

Zainstalowane CLI spoza repo, z innym `PYTHONHASHSEED=1129` i niepoprawnym
`APP_ENV`, odtwarza indeks ze źródeł osobnych klonów obu repo. Indeks, profil
ze zgodami i approved preflight są identyczne bajt po bajcie. Raport workera
odtworzono we wszystkich polach poza nowymi czasami pomiaru i wynikającym
z nich report ID. Metryki, wyniki wszystkich pytań, cytaty, zgody i niezaliczona
bramka pozostają identyczne. Artefakty odtworzenia mają `0600`; checksumy kodu,
kontraktów i przypiętych wejść zgadzają się z odbiorem.

Nie wykonano push ani AWS.
