# Wersjonowane pytania AI12

[Profil proponowany](../agent/question-routes.proposed.v1.json) obejmuje 26 pytań:
20 biznesowych w języku polskim i angielskim oraz 6 dokumentacyjnych z zachowanymi
regułami dowodów. Obsługuje 12 intencji: sprzedaż, porównanie sprzedaży, zapas,
prognozę, ryzyko, anomalie, operacje, modele, dokumentację, stan zweryfikowany,
badanie sygnałów i sugestie do przeglądu. Nie oznacza to 26 odpowiedzi
zakwalifikowanych na rzeczywistych danych lub przez Sonnet.

`ReviewedPlanner` wybiera intencję wyłącznie według całego zarejestrowanego pytania.
Normalizuje Unicode NFC, wielkość liter i odstępy. Nie interpretuje dodatkowego
tekstu ani parametrów zakresu z pytania. Nieznane pytanie kończy się 422. Jawne
żądanie zapisu/sekretu jest przekazywane do istniejącej odmowy bez wywołań
narzędzi. Caller nadal nie może przekazać intencji, principal, modelu ani narzędzia
w [AssistantQuery](../contracts/assistant/v1/query.v1.schema.json).

Profil wiąże się z dokładnym graph config ID. Pytania dokumentacyjne wymagają
reguły dowodowej o tym samym pytaniu i intencji. Duplikaty po normalizacji są
odrzucane. `labels_state=proposed` jest dopuszczony wyłącznie w środowisku `test`
z jawnym `allow_proposed=True`; runtime lokalny wymaga zaakceptowanych etykiet.
Zmiana pola akceptacji zmienia profile ID i wymaga ponownej kwalifikacji.

Planer sprawdza rolę, `assistant:query`, cały zakres sprzedaży, kanał oraz wszystkie
prawa wymaganych narzędzi przed admission. Brak adaptera daje 424. Sprawdza
produkty i jednoznaczne przypisanie kanału w katalogu zweryfikowanego importu
Source dla każdego dnia żądanego okresu. Nie mapuje aliasów sklepów ani magazynów.
Obsługiwane kanały pozostają `store`/`online`, zgodnie z obecnym kontraktem narzędzi;
rozszerzenie na `marketplace`/`wholesale` wymaga osobnej kwalifikacji tych kontraktów.

Dla porównania sprzedaży wyznacza bezpośrednio poprzedzający, rozłączny okres
o tej samej liczbie dni i sprawdza oba okresy. Obserwacje nie mogą mieć okresu
w przyszłości. Prognoza, ryzyko i sugestie używają końca ostatniego zamkniętego
dnia UTC oraz horyzontu 1–14 dni względem tego punktu odcięcia. Jest to zakres
zapytania; dostępność opublikowanego wyniku musi potwierdzić adapter. Dane
katalogowe dostępne po tym punkcie odcięcia nie są używane.

`reviewed_backend` łączy planer z istniejącym `GraphAssistant` i fabryką grafu.
Nie tworzy klienta AWS. Identyfikator runtime wiąże profil pytań, graph config,
code hash, katalog Source, kanał, środowisko, rodzaj źródeł i listę adapterów.
Przed wykonaniem sprawdza zgodność rzeczywistego zestawu adapterów z deklaracją.
Fixtures nie mogą być przedstawiane jako źródła runtime. Budżety, walidacja
dowodów, trace store i trwały zapis pozostają kontrolowane przez istniejący graf
i Assistant API.

Wznowienie nie rejestruje automatycznie rzeczywistych adapterów biznesowych.
Ich kontrakty, świeżość, snapshot/model/release binding oraz fizyczne zakresy
stockout muszą zostać sprawdzone przy integracji z AI10. Brakujący adapter jest
przeszkodą, a nie pustym wynikiem lub zerową wartością.

Walidacja offline:

```bash
make agent-security-test
make agent-evaluate PROVIDER=fake
make contracts-check
```

Aktualny kandydat offline używa
[nowego release](../agent/evaluation-release.fake.resume.v1.json). Historyczny
[release](../agent/evaluation-release.fake.v1.json) i wyniki Bedrock pozostają
zachowane; ich kwalifikacja nie przechodzi automatycznie na nowy kod.
