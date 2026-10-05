# Stockout — paczka odbioru i MLflow

Przenośna receptura zawiera preprocessing, klasyfikator i kalibrator. Oddzielna
polityka wskazuje ten sam model i kalibrator. Odbiór nie fituje nowych parametrów.

Paczka testowa zawiera 20 plików: approval/qualification/recipe/policy/model_card/
smoke/inputs/signature oraz 12 raportów review gates. Produkcyjna kwalifikacja
wymaga dodatkowo final_quality, campaign_freeze i campaign_permission. Całość
ma limit 16 MiB, prywatne katalogi 0700 i zwykłe pliki 0600, bez symlinków,
braków i dodatkowych plików. SHA oraz rozmiar wiążą każdy raport i artefakt.

Verifier odtwarza cały wynik smoke z tej samej receptury, polityki i publicznych
punktów wejściowych. Provisional smoke zawsze używa namespace test-mechanics,
ma serving_eligible=false i nie zastępuje rzeczywistego release. Signature
przypina wejściowy i wyjściowy JSON schema oraz fizyczny klucz produktu/zapasu/
origin. Karta wiąże recepturę, politykę i status jakości. Kwalifikacja wygasa
najpóźniej po siedmiu dniach; historyczny backup można zweryfikować z current=false.

Produkcyjny verifier ponownie sprawdza dokładną kampanię, zgodę właściciela,
recepturę i wszystkie sześć raportów końcowych. Niepełny lub niezaliczony zbiór
nie jest paczką modelu gotowego do serwowania. Zgoda na kampanię nie jest
promocją; 12 raportów odbioru oraz osobna decyzja lifecycle nadal są wymagane.

MLflow korzysta z istniejącego transportu loopback/Compose. Tylko stockout
namespace jest dopuszczony; namespace test-mechanics wymaga APP_ENV=test.
Import weryfikuje prywatną paczkę przed utworzeniem run, przesyła małe artefakty,
sprawdza ich zdalne SHA i dopiero potem oznacza run jako verified/FINISHED.
Ponowny import znajduje ten sam run; niekompletny run po utracie odpowiedzi
blokuje ponowny POST. Import nie rejestruje wersji ani nie zmienia aliasów.

Rejestracja i validate wiążą model name/version, run ID, immutable URI i approval
SHA. Aliasami mogą być wyłącznie candidate/champion/rollback. Decyzje nadal
prowadzi wspólny, odzyskiwalny protokół AI 05 oraz dziennik PostgreSQL AI 08.

Rzeczywisty checker stockout lifecycle używa odizolowanego serwera MLflow
z kontrolowanego runnera AI 05. Utrata odpowiedzi jest wstrzykiwana po faktycznym
zapisie wersji/aliasu. Restart/pełny backup muszą zachować bajty paczki, wersję,
aliasy, DB head i zakończone decyzje. Kwalifikacja testowej paczki nie dowodzi
niezależnej jakości klasyfikatora ani gotowości całego AI 08.
