# AI 09.8 — fizyczny odczyt etykiet pięciu ról

[Czytnik](../forecast-outcome-reader.md) zachowuje pełną populację kluczy,
wybiera wersję znaną na cutoff, sprawdza dojrzałość i eligibility wewnątrz
trwałej rezerwacji audytu. Niezależna ocena, portfolio final test i promocja
pozostają zablokowane. Cały etap: `in_progress / not_ready`.

## Odbiór native i zainstalowanego pakietu

Kontrolny physical feature/history fixture ma 65 dni origin i 865 kluczy:
61 w pięciu rolach i 804 purged. Zawiera rzeczywiste pliki Parquet, ale jego
źródło i etykiety są kontrolnymi danymi testowymi. Dowód ma jawne positive,
zero, missing i niekompletną nowszą wersję. Nie jest odbiorem źródła projektu
ani benchmarkiem jakości modelu. Zachowano częściowe horyzonty występujące
w rodzicu, bez przycinania kluczy do wspólnego przecięcia.

Native i oddzielnie zainstalowany wheel dały identyczne liczniki oraz
content SHA każdej roli:

| Rola | Wszystkie klucze | Scoring eligible | Censored label |
|---|---:|---:|---:|
| calibration | 14 | 14 | 0 |
| development_evaluation | 5 | 5 | 0 |
| early_stopping | 14 | 14 | 0 |
| train | 14 | 12 | 2 |
| tune | 14 | 14 | 0 |

Wszystkie 277 modułów źródłowych mają identyczne bajty w wheel. 42 importowane
moduły konsumenta pochodzą z instalacji; TensorFlow/Keras nie są importowane
przez tę ścieżkę. Cztery schematy v6 są spakowane i poprawne; unit odbiór
oraz audytowany wheel schema probe walidują cztery rzeczywiste typy
rekordów oraz wszystkie definicje schematów.

Zachowano 14 oryginalnych plików metadata history i osiem plików feature
parent. Fingerprint cech: `11733b2f10d4d52315645239d96eb9bfcc29a4b561b213f16f02375539bf010f`.
Rzeczywiste raw/curated, stare development labels i portfolio final test
nie były otwierane w tym przyroście; nowych fitów na danych projektu: 0.

## Trwałość i zasoby

Kontrolny dziennik ma 12 rezerwacji: 11 completed i 1 failed po zmianie
physical bytes. Kolejny CLI z nieistniejącą ścieżką wejścia odrzucono przed
jej otwarciem; plik dziennika nie zmienił się po odmowie. To liczniki współpracujących czytników, obejmujące też końcowy schema
probe wewnątrz audytowanego kontekstu. Budowa fixture operuje na znanych
kontrolnych danych i nie jest przedstawiana jako kompletny audyt całej
maszyny. Wcześniejszy prototypowy odbiór zachowano osobno.

Właściwy `/private/tmp/ai09-development-outcome-journal` zachował tę samą
politykę i wszystkie historyczne wpisy. Zachowuje trzy plany prototypów i rejestruje czwarty, zamrożony plan
czytnika dla kontrolnego fixture, z pełnym końcowym runtime, bez
odczytu jego etykiet w dzienniku projektu. Rezerwacji projektu nadal 0,
pozostały budżet 64. Nie resetowano historii ani limitów przy zmianie kodu.

Najwyższy zmierzony peak RSS własnego drzewa procesu: **132.02 MiB**.
Najwyższy scratch próbkowany co 0.05 s: **2499819 B / około 2.38 MiB**.
Pojedyncze role native i wheel trwały około 3.37–3.93 s z importami.
Pomiar obejmuje CLI, replay rodzica, prywatne dane i journal I/O; nie
obejmuje przygotowania fixture i wcześniejszego budowania podziału.
Obowiązywały limity 1 GiB RSS / 512 MiB scratch / 120 s na komendę oraz
50 GiB wolnego dysku. Trwały równoległe otwarte sesje i CI, więc czasy
nie są porównaniem wydajności ani gwarancją większego profilu.

## Regresja i stan publikacji

**181 testów** tej ścieżki i regresji przeszło, w tym **60 nowych**.
Sprawdzają faktyczną kolejność I/O, wszystkie role/cel, późne i niekompletne
wersje, kalendarz i historię, pełne klucze, semantyczne błędy po resealing,
zmiany rodziców i bazy, iterator po zamknięciu, failures/replays, limity,
SIGKILL, zamknięcie bazy i fizyczny Parquet. Wersje oraz ilości spoza
zakresu fizycznego int64 są odrzucane przed zapisem do indeksu. Reguły eligibility porównano z istniejącym
forecast qualification. Mypy/lint i kontrakty przeszły dla końcowego kodu.

Pełny `make ci-local` przeszedł: **2020/2020 testów głównych** w 1693.32 s
oraz **3/3 rzeczywistych testów TensorFlow CPU** w 79.48 s, bez skips.
Lint/format, mypy (352 pliki), wszystkie kontrakty i bramki danych, package,
Compose config oraz oba skany sekretów przeszły. Całość: **2003.74 s /
33 min 23 s**. Freeze 806 plików źródła/testów/kontraktów pozostał identyczny. Pierwszy start zakończył się po 1.12 s
na formatowaniu przykładu Python w dokumentacji, przed testami. Kolejny
start przerwano po 605.94 s wyłącznie we własnym drzewie CI, aby poprawić
regułę dojrzałości przed dalszym odbiorem. Jeszcze jeden start przerwano po 478.09 s we własnym CI na przegląd
zamykania zasobów; odbiór iteratora ujawnił potrzebę sprawdzenia kontekstu
przed pobraniem następnego wiersza z już zamkniętej bazy. Zmiana ma osobny
zaliczony test. Nieukończone próby i ich logs zachowano; nie są dowodem CI końcowej wersji. Dojrzałość zależy od cutoff
po wymaganym delay, a nie od sztucznie późnego availability dostawy; nowy
test zachowuje kompletne dane dostarczone tuż po zamknięciu target day. Wynik końcowej wersji i zachowane próby podaje
[receipt](09-08-forecast-outcome-reader.json).
Commit rodzica `08fdc52` ma zielone Required CI166. Nowy commit wymaga
osobnego zdalnego Required CI; draft PR #18 pozostaje draft.

## AI 07/08 i otwarty zakres

Przed implementacją odczytano AI 08 `92b8d5f` i jego in-flight 08.13;
później ta sesja opublikowała `4faaf4b`. Wykorzystano istniejące forecast
Parquet/history i ograniczony SQLite, bez kopiowania stockout physical
readerów. AI 08.13 raportuje pojedynczy replay rodziców i krótszy assembler,
nie niższy RSS ani gotowość całego etapu. AI 07 przy odczycie: `8581230`.
Żaden plik, branch, proces ani stos tych sesji nie był przez AI 09 zmieniany.

Pozostają: audytowany eksporter i full source-parent replay, complete exposure
inventory obejmujący raw/curated/history, świeże rzeczywiste okna, integracja
pięciu ról z treningiem i wspólnym fit budget, niezależna kalibracja/ocena,
większy profil, uncertainty i robustness oraz końcowe AI 07/08 i lifecycle.
Chronologiczny podział i zgodny hash nie ustanawiają świeżości holdoutu.
