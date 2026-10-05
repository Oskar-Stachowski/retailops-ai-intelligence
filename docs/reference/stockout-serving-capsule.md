# Stockout — paczka odbioru i MLflow

Przenośna receptura zawiera preprocessing, klasyfikator i kalibrator. Oddzielna
polityka wskazuje ten sam model i kalibrator. Odbiór nie fituje nowych parametrów.

Paczka testowa zawiera 20 plików: approval/qualification/recipe/policy/model_card/
smoke/inputs/signature oraz 12 raportów review gates. Produkcyjna kwalifikacja
wymaga dodatkowo final_quality, campaign_freeze, campaign_permission, selection
i execution_evidence: łącznie 25 plików. Całość
ma limit 16 MiB, prywatne katalogi 0700 i zwykłe pliki 0600, bez symlinków,
braków i dodatkowych plików. SHA oraz rozmiar wiążą każdy raport i artefakt.

Verifier odtwarza cały wynik smoke z tej samej receptury, polityki i publicznych
punktów wejściowych. Provisional smoke zawsze używa namespace test-mechanics,
ma serving_eligible=false i nie zastępuje rzeczywistego release. Signature
przypina wejściowy i wyjściowy JSON schema oraz fizyczny klucz produktu/zapasu/
origin. Karta wiąże recepturę, politykę i status jakości. Kwalifikacja wygasa
najpóźniej po siedmiu dniach; historyczny backup można zweryfikować z current=false.

Produkcyjny verifier ponownie sprawdza dokładną kampanię, zgodę właściciela,
recepturę i wszystkie sześć raportów końcowych. Przelicza segment/scenario gates
z metryk i odtwarza całą kartę z zamrożonego development oraz końcowych receipts.
Niepełny lub niezaliczony zbiór
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

## Komendy końcowego odbioru

Najpierw jawnie zatwierdzona kampania tworzy sześć receipts native/wheel/access/
resources. `collect_stockout_final.py` zbiera ich publiczne raporty bez ponownego
odczytu końcowych etykiet. Workflow final sam zbiera ten sam Git SHA i run ID.
Niezaliczone bramki pozostają w final_quality ze statusem not_ready.

`stockout_release.py qualify` wymaga zaliczonej kampanii, pełnego zamrożonego
selection, publicznych rodziców curated/features/upstream oraz jawnego fizycznego
scope/origin. Pełny replay działa tylko na własnym runnerze GitHuba z rezerwą
6 GiB. Kwalifikacja jest ważna jeden dzień, nie zmienia progu ani rejestru.

Oddzielny approve czyta request oraz dwanaście prywatnych `reports/<gate>.json`.
Każdy raport musi wskazywać tę samą qualification_id, własny gate, status passed
i rzeczywiste dowody odbioru. Request przypina SHA/rozmiary raportów i faktyczny
image digest. Nie wolno zastępować niezakończonego odbioru ręcznym wpisaniem passed.
Promoter uwierzytelnia się przez prywatne policy/credential files.

```text
python scripts/stockout_release.py qualify --freeze <file> --permission <file> --recipe <file> --policy <file> --receipts <dir> --selection <file> --curated <dir> --features <dir> --upstream <dir> --scope <file> --as-of <UTC> --output <private-dir>
python scripts/stockout_release.py approve --qualification <dir> --request <file> --reports <private-dir> --policy-file <file> --credentials-file <private-file> --output <private-dir>
python scripts/stockout_release.py verify --capsule <private-dir> --approval-id stockout-approval-sha256-<64hex>
python scripts/mlflow_stockout_lifecycle.py --policy-file <file> --credentials-file <private-file> --env-file <private-file> upload --capsule <private-dir> --approval-id stockout-approval-sha256-<64hex> --work-dir <private-dir>
python scripts/mlflow_stockout_lifecycle.py --policy-file <file> --credentials-file <private-file> --env-file <private-file> decide < <explicit-reviewed-decision.json>
```

Upload importuje zweryfikowane bajty do osobnego MLflow run bez rejestracji/aktywacji.
Każde register/reject/promote/rollback jest osobnym, uwierzytelnionym requestem
zgodnym ze stockout lifecycle. Zgoda na końcową kampanię nie jest zgodą na promote.
Komendy nie zapisują credentials w argumentach ani w wyniku.
