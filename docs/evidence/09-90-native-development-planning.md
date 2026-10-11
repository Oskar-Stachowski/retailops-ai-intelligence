# AI 09 — ordinary Source przed planowaniem scenariuszy

Wykonawca v30 wymaga konkretnych planów anomalii przed generacją trzech
wariantów. Ich rzeczywiste identyfikatory produktów, lokalizacji i wykonalne
interwencje trzeba jednak wybrać z poprawnego ordinary Source. Nowy, osobny
protokół v31 umożliwia tę wcześniejszą pracę bez deklarowania fikcyjnych
identyfikatorów lub wcześniejszego otwierania final.

`compile_native_development_planning` przypina pełny profil 25 albo 50 produktów,
365 dni, 5 par sprzedaży, 3 lokalizacje zapasu i seed 42. Przed generacją
zamraża wszystkie pięć ról, dotychczasowe koszty, przyszłe polityki oceny,
czysty commit i locki producenta oraz hash natywnego modułu planera. Dziennik
dopuszcza dokładnie jedną generację ordinary i jeden zależny odczyt Source.

`generate_ordinary_for_planning` wywołuje dotychczasowe sześć faz generacji,
kwalifikacji, eksportu, importu, kuracji i weryfikacji. Dopiero po ukończeniu
`plan_native_development_scenarios` może zarejestrować odczyt i uruchomić
planer w osobnym interpreterze producenta. Natywny helper czyta całe Source,
sprawdza wszystkie tabele oraz raporty i oddaje plany demand/physical.
Wymagane są oryginalna tożsamość Source i zgodna proweniencja producenta.

Koszt pierwotnej generacji pozostaje w dzienniku i potwierdzeniu planowania.
Jej faktyczny czas odejmuje się od łącznego limitu przygotowania, maksymalnie
180 minut. Obowiązują górne limity 12 GiB RAM i 8 GiB scratch, rezerwy co
najmniej 1 GiB RAM i 6 GiB dysku oraz jeden wątek CPU. Błąd i niedokończona
próba zużywają uprawnienie. Nie ma automatycznych ponowień.
Prywatny wynik i jego hash są zapisywane przed zakończeniem operacji;
weryfikacja wymaga także zachowanego potwierdzenia pierwotnej generacji.

[Wyniki kontroli](09-90-native-development-planning.json): 29 końcowych testów
nowej ścieżki oraz 70 testów tej ścieżki i regresji z zainstalowanej paczki
przeszło bez pominięć. Sprawdzono m.in. rzeczywisty dziennik, sześć faz,
kolejność operacji, równoczesne rezerwacje, zmienione potwierdzenia i plany,
budżet czasu oraz osobny proces z błędnym pinem, lockiem i proweniencją.
Procesy kontrolne używają jawnie sztucznego producenta. Nie dowodzą efektów
natywnych scenariuszy ani jakości Project. Dwie błędne asercje oczekiwanych
odmów poprawiono; nieudane wyniki zachowano w dowodzie.

Paczka zawiera 629 modułów identycznych ze źródłami i trzy nowe kontrakty v31.
Starsze kontrakty zachowują swój zakres. Nowa ścieżka nie zezwala na trening,
wybór modelu, final ani zamknięcie kampanii.

Producent pełnych scenariuszy jest scalony przez
[Source PR 113](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/113),
z zakończonym odbiorem wynikowego main `95aa6f0e` (21 sukcesów i cztery
deklarowane pominięcia ścieżek). Sam planer jest w
[Source PR 114](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/114).
Jego poprawiona lokalna próba natywna nie wystartowała z powodu braku RAM;
pełne Required CI musi potwierdzić jej wynik. Aktywny pin Source pozostaje
niezmieniony do odbioru potrzebnego kodu.

Opisany w [09.91](09-91-resolved-development-variants.md) wykonawca dodaje
jawny replay ordinary oraz generację demand/physical, zachowując koszty.
Następnie trzeba wykonać rzeczywiste przygotowanie i jawnie przenieść
zweryfikowany ordinary parent do przygotowania trzech wariantów oraz prób
modeli, zachowując koszt generacji i rejestrując nowe odczyty. Nadal potrzebne
są zamrożone uczciwe próby, niezależny wybór finalistów, pełne profile,
wszystkie grupy krytyczne i trzy zastosowania, niepewność, raporty oraz
lifecycle. Liczby generacji pełnych małych profili, treningów Project i nowych
odczytów final wynoszą nadal zero. Canonical pozostaje wyłączony, AI 09
pozostaje `not_ready`.
