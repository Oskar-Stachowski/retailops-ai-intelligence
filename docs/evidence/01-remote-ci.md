# Zdalny odbiór fundamentu AI 01

Pomiar 28.09.2026, GitHub Actions. Oba repozytoria przeszły Required CI
po chronionym scaleniu PR i zdarzeniu **push na main**.

| Repozytorium | Odebrany main SHA | Przebieg | Wynik |
|---|---|---|---|
| retailops-cloud-native-platform | `a0a83c4c82519d5258eb3144e0fef0a68fc909a4` | [Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/36394194602) | 24 jobs success |
| retailops-ai-intelligence | `f2c047b85ba7a2c3eeed6428a545ec8ef6bb79de` | [Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36395139229) | 4 jobs success |

Odbiór dotyczy wskazanych rewizji; późniejszy commit aktualizuje dokumentację.
Wyniki maszynowe zawierają zdarzenie, SHA i każdy job: [JSON](01-remote-ci.json).

Repo AI: Ubuntu 24.04 / Linux AMD64, Python 3.11.15, uv 0.12.19.
`make bootstrap check` wykonuje 273 testy bez pominięć (9.86 s), lint/format,
Mypy (48 plików), dokumentację, kontrakty, wheel/sdist i Compose config.
Jobs checks, secrets, persistence i required-result mają success.
`make bootstrap compose-smoke` sprawdza rzeczywisty PostgreSQL/pgvector i MLflow:
oddzielne bazy/role, jawne migracje, trwałość rekordu i artefaktu po SIGKILL
i down/up, health 200 oraz ready 503 podczas awarii DB i ready 200 po recovery.
Sprawdza loopback portów, brak publikacji DB i brak wygenerowanych sekretów
w logach usług. Modele, RAG, agent, produkcyjne IAM i AWS nie są częścią odbioru.

RetailOps: pełna macierz Required CI na runnerach Linux, w tym API/frontend,
data quality, Docker smoke/recovery/rollback, kind runtime, Terraform i skany.
Odbiór CI nie zamyka ustaleń źródła DATA-01–05 ani nie kwalifikuje RF do serving.

Odczyt API GitHub potwierdza w obu repo main protected, required-result od
GitHub Actions, strict/up-to-date, PR-before-merge i enforce_admins.
Nie osłabiono tych ustawień. W nowym repo AI automatyczne wyzwalanie ruszyło
po ponownym zapisaniu niezmienionej konfiguracji Actions (enabled=true,
allowed_actions=all) i zdarzeniu reopened. Workflow jawnie określa zdarzenia
PR i branche push; sześć testów negatywnych chroni ten zakres.

Wcześniejszy workflow_dispatch potwierdził runtime Linux, lecz GitHub nie
zalicza go jako wymaganej kontroli PR. Odbiór opiera się na automatycznych
przebiegach PR i push, zgodnie z [zasadami GitHub](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).
Lokalne pomiary macOS/ARM64 zachowują własne daty, rewizje i zakresy.
