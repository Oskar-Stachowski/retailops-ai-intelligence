# AI10 — odtworzenie oryginalnego v12 bez naruszenia local disk reserve

Status: **in_progress**. Oryginalny finalny model pozostaje właścicielsko
zaakceptowanym development deliverable z quality/model `not_ready`. Odtworzenie
archiwum nie nadaje produkcyjnej jakości ani uprawnień do deploymentu.

1. Sprawdzić publiczne przypięcie
   [ai10-v12-original-archive.json](reference/ai10-v12-original-archive.json).
   Wymaga ono wszystkich **664** oryginalnych plików i **31 994 707 803 B**,
   dodatkowo dokładnych bajtów frozen wheel, locka, pyproject, source constraints
   oraz oryginalnej decyzji właściciela. Manifest runu ma SHA-256
   `29bf6837ca2cb7504213168743239b037041f375763bc8f01ae28c1f6d09b26e`.
2. Operator z istniejącym dostępem do oryginalnego S3 tworzy osobny losowy
   task prefix `ai10-native-v12-read-handoff/<uuid>/`. Umieszcza tam wyłącznie
   pięć support files i prywatną mapę read URLs. Oryginalnego archive prefixu
   nie zmienia. Nowe obiekty wymagają private access, SSE i create-only writes.
3. Podpisać tymczasowe `GetObject` URLs dla dokładnie przypiętych plików.
   Zachować je w prywatnej read map, a URL tej mapy zaszyfrować jako jedyny
   task-owned repo secret `AI10_V12_READ_HANDOFF_<uuid>`. Nie przenosić kluczy
   konta AWS do runnera. Nie zapisywać signed URLs w Git, receipts lub logach.
   Własność prefixu/secretu oraz deadline zachować w prywatnym state file.
4. Uruchomić [AI10 original v12 archive](../.github/workflows/ai10-v12-original-archive.yml)
   na dokładnym commicie `ai/10-ready`, przekazując nazwę secretu i SHA-256
   prywatnej mapy. Downloader pozwala wyłącznie na HTTPS do przypiętego bucketu,
   dokładne object keys oraz pełny census. Redirect, brak/extra file, zmienione
   bytes, niezgodny hash i niewystarczający disk reserve przerywają odbiór.
5. Domyślny local reserve pozostaje **50 GiB**. Niższy **6 GiB** dopuszcza
   wyłącznie disposable GitHub-hosted Linux runner. Preflight wymaga wolnego
   miejsca na pełny archive i reserve przed utworzeniem outputu. Istniejący
   output nigdy nie jest zastępowany. Niepełny nowy output jest usuwany.
6. Zainstalować oryginalny wheel z SHA-256
   `c56a18ce09e0007bcd0f9e571541de4d8fe1d1e644a39e18b4973e15c122a14f`
   w jego odrębnym frozen runtime. Bieżący AI10 wywołuje istniejący pełny
   `load_evidence`, oryginalny installed-wheel verifier i ponowne byte checks.
   Nie zastępuje oryginalnych historycznych hashów aktualnym kodem.
7. Zachować wyłącznie byte census i semantic verification receipt. Upload
   nie zawiera 32 GB archive, read map ani credentials. Raport jawnie rozdziela
   weryfikację archiwum od jeszcze wymaganego native forecast serving i Source
   API/UI; `serving_acceptance=false` pozostaje do ich rzeczywistego wykonania.
8. Po zakończeniu albo błędzie usunąć dokładnie własny tymczasowy secret i
   sześć własnych obiektów handoff/support. Oryginalne 664 obiekty i retained
   release pozostają. Powtórzenie wymaga świeżego ograniczonego read handoff.

Kontrole lokalne bez pobierania pełnego archiwum:

```sh
uv run --locked --extra snapshot --extra forecast python -m pytest -q tests/test_ai10_v12_archive_recovery.py
make type-check docs-check
```
