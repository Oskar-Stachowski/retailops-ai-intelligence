# Zdalny odbiór fundamentu AI 01

Pomiar 28.09.2026, GitHub Actions, Ubuntu 24.04 / Linux AMD64.
[Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36393431781)
na `2f067c68cb5931ef31578f724e496f5381b3ae6a`, zdarzenie `workflow_dispatch`:
checks, secrets, persistence i required-result mają `success`.

`make bootstrap check`: 267 testów bez pominięć, 8.86 s; Ruff/format,
Mypy (48 plików), dokumentacja, kontrakty, pakowanie i Compose config przechodzą.
`make bootstrap compose-smoke` potwierdza odrębne bazy/role i pgvector 0.8.6,
jawne migracje, trwałość rekordu AI i artefaktu MLflow po SIGKILL oraz down/up,
health 200 i ready 503 podczas awarii DB oraz ready 200 po jej powrocie.
Porty są loopback, DB nie ma portu hosta, metryki wymagają tokenu; sprawdzono
brak wygenerowanych sekretów w logach usług. Wyniki maszynowe: [JSON](01-remote-ci.json).

Ten przebieg potwierdza runtime Linux, ale nie jest kontrolą zdarzenia PR lub
push na main. GitHub nie zalicza workflow_dispatch jako wymaganej kontroli PR;
[zasady GitHub](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).
Odbiór publikacji pozostaje otwarty do automatycznego Required CI i scalenia
z zachowaniem ochrony main. Nie zastępuj tego warunku ręcznym statusem success.

Zakres nie obejmuje AWS, produkcyjnego IAM, backup/restore, modeli, RAG ani agenta.
Lokalne pomiary macOS/ARM64 w pozostałych raportach zachowują własne daty i rewizje.
