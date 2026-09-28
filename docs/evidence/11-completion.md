# Końcowy odbiór AI 11

**2026-09-28 · Etap 11 completed — lokalny semantyczny RAG.** Implementacja:
`6790f485cfa879b0336dbf1f710f30adea06c6d0`. [Zapis pomiarów i checksum](11-completion.json),
[instrukcja](../knowledge-semantic.md), [aktualny status](../STATUS.md).
Brak otwartych blokad Etapu 11.

## Zakres

Zatwierdzony snapshot 29 dokumentów i 451 fragmentów z obu repo zachowuje
źródła AI `082bed4` i RetailOps `78f801f`, statusy, access i cytaty.
Wektor obejmuje nagłówki oraz treść; różne konteksty mają osobne wpisy cache.
Provider: **Amazon Titan Text Embeddings V2**, `eu-north-1`, 1024 wymiary,
float32/unit/cosine. Rzeczywisty model wywołano w AWS; CI nie korzysta z AWS.

Golden zawiera te same 44 pytania, 9 przypadków krytycznych i zamrożone progi.
Nie zmieniono pytań, oczekiwanych/zabronionych sekcji, scope, narzędzi ani progów.
Zgoda `approved_pipeline` przepisuje wyłącznie powiązania nowego indeksu
i konfiguracji; receipt wskazuje pierwotną zgodę i profil.

| Metryka | Wynik | Wymaganie |
|---|---:|---:|
| Recall@5 | 0,8529411765 | ≥ 0,80 |
| MRR | 0,6612745098 | ≥ 0,60 |
| Przypadki krytyczne | 9/9 | 100% |
| Powiązanie cytatów | 100% | 100% |
| P95 całego API → Bedrock → PostgreSQL | 359,12 ms | ≤ 1000 ms |

Wcześniejszy wariant bez nagłówków nie przeszedł jakości. Dodanie kontekstu
sekcji i selekcji według trafności rozwiązało utratę informacji o sekcjach;
nie użyto wyników rankera do przepisywania odpowiedzi wzorcowych.
Pojedyncze zimne zapytanie trwało 1729,14 ms. P95 powyżej jest pomiarem
wszystkich 44 żądań, a nie tego jednego czasu ani samego odtworzenia offline.

## Pełna ścieżka i trwałość

Aktywny lokalny indeks:
`index-sha256-d193c015d0815215725156145cef5ed21553452e1c235d0c8e4cabece96d9f46`.
Run `run-ad4e22256eee6be6fe415bb848e46589` ma `succeeded`. Worker odtworzył
zatwierdzony snapshot i metryki bez AWS. Kwalifikacja ponownie odtworzyła
raport, sprawdziła zgody i przegląd podobieństw, a osobna aktywacja zapisała
generację **1** w `local/retrieval`.

PostgreSQL odtworzył wszystkie 44 wyniki, łącznie z dokładnymi chunk IDs.
Następnie 44 żądania przez API z rzeczywistym Bedrock i PostgreSQL dały te
same wyniki. Zrobiono 38 wywołań modelu; pozostałe żądania zostały odrzucone
przez kontrolę scope. Osobny smoke potwierdził 401 bez poświadczeń, 403 dla
admina bez odczytu, 200 dla czytelnika, 200 admin/current i 403 na administracji
dla zwykłego czytelnika. Current wskazuje pełny raport golden udanego runa.

Pełny Compose na `0008_rag_semantic` potwierdził aktywację, replay, CAS,
rollback i odczyt starego pinu. Nieudany run zachowuje raport, ale nie podlega
kwalifikacji; SQL odrzuca kwalifikację bez release. Przeszły dotychczasowe
testy integralności/atomowości, live deny, concurrency, awarii workera oraz
SIGKILL/down-up i zachowania runów/raportów. Fixture semantyczny ma jawne
syntetyczne wektory; rzeczywistą jakość potwierdza osobny pomiar opisany wyżej.
Właściwy indeks pozostał dostępny po ponownym uruchomieniu kontenera DB.

## Weryfikacja kodu

- Pełna regresja: **643 testy**, 204,22 s; po dopracowaniu current/report
  ponowiona właściwa regresja: **77 testów**, 52,84 s.
- Ruff/format, strict Mypy dla 100 plików, linki, kontrakty i schemas, wheel/sdist
  oraz Gitleaks przechodzą.
- Wersjonowane są konfiguracje, golden/zgody i dowody. Korpus, wektory, pełne
  profile i poświadczenia pozostają prywatne poza Git.

Wywołania AWS mają limit żądań/tekstów, timeout i brak automatycznych retries.
Przygotowanie zaakceptowanego wariantu zużyło 489 żądań / 68157 tokenów;
odbiór online 38 żądań / 793 tokeny, osobny smoke 1 żądanie / 17 tokenów.
Te liczniki nie obejmują odrzuconego wcześniejszego wariantu bez nagłówków
i nie są kwotą faktury. Odtworzenie offline nie wywołuje modelu i ma koszt modelu 0.

## Granice

Etap 11 kończy wyszukiwanie z zatwierdzonego snapshotu, nie generowanie odpowiedzi.
Groundedness i wykonanie narzędzi należą do AI 12; pełny agent wymaga także AI 10.
To lokalny odbiór i kontrolowane wywołania Bedrock, bez wdrożenia AWS/EKS.
Źródła nie aktualizują się samoczynnie po zmianie `main`; kolejna wersja wymaga
przeglądu i nowej ewaluacji. Hash zapewnia integralność, nie podpis decyzji.
Produkcyjny IAM, budżet wielu replik i wdrożenie pozostają dalszym zakresem.
