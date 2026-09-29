# Odbiór drugiego zakresu AI 12

**2026-09-29 · konfiguracja/prompty i bounded fake chat.** Branch `ai/12-tools`,
po zakresie narzędzi `aa01929`. [Instrukcja](../agent-chat.md),
[bieżące bramki](../STATUS.md), [zapis kontroli](12-chat.json).

Konfiguracja przypina model, region, parametry, budżet, sześć promptów,
schematy narzędzi/draftów, indeks i embeddings/retrieval AI 11. Zmiana wersji
zmienia config ID. Snapshoty kontraktów i CLI sprawdzają te powiązania offline.
`ScriptedChatProvider` jest jawnie testowy i stosuje dokładny checksum request.

## Kontrole

Pełna regresja: **819 passed**, 131,94 s. Właściwy zestaw agent tools/chat:
**176 passed**, 6,27 s, w tym **67 nowych przypadków chat**.
Ruff/format (185 plików), strict Mypy (112 plików), kontrakty, dokumentacja,
wheel/sdist, Compose config i Gitleaks przechodzą. Test wheel poza źródłami
potwierdza obecność wszystkich sześciu promptów oraz działanie config-check.
Config ID i checksum plików podaje [zapis kontroli](12-chat.json).

Nowe testy sprawdzają:

- Wersjonowanie konfiguracji, drift schematów/promptów, zamknięte ścieżki,
  duplikaty JSON, rozmiar, nieprawidłowe limity i brak wywołania providera z CLI.
- Ścisłe drafty i istniejący serwerowy scope/capabilities; plan nie wykonuje
  narzędzi. Polecenie zapisu, SQL/URL i identity w output są odrzucane.
- Referencje do uprawnionych wyników, dokładne cytaty/statusy i freshness;
  podmiana retrieval config i kontekstu podczas odpowiedzi nie daje nowych praw.
- Rozróżnienie `no_data` i faktu; odrzucenie wymyślonego source_ref/as-of,
  niepobranego cytatu, niezatwierdzonego obliczenia i modelowej akcji.
- Wspólny deadline i limity prób/tokenów/kosztu, concurrency, anulowanie,
  rozliczenie usage, rezerwację nieznanego kosztu oraz wspólny smoke cap.
- Ograniczony retry throttle/transient, brak retry auth/schema/timeout,
  jedną naprawę tego samego kontekstu oraz bezpieczne kody/audit bez raw output.

## Granice

To przygotowanie punktu 2, nie pełny odbiór punktu 2 z real Bedrock ani AI 12.
Nie wykonano AWS chat, nowej kwalifikacji embeddings, zapisu DB czy sugestii
w RetailOps. Test wiedzy używa syntetycznych wektorów i pinned backend spy;
nie jest nowym pomiarem jakości Titan. Syntetyczne stawki nie są cennikiem AWS.

Drafty nie są końcową zweryfikowaną odpowiedzią Assistant API. Pełne sprawdzenie
liczb/znaczenia źródeł, graf, polityka sugestii, golden odpowiedzi, trwały trace,
admission i circuit breaker pozostają otwarte. Rzeczywisty adapter i ograniczony
smoke chat oraz działające źródła i E2E sugestii z AI 10 są dalszymi bramkami.
Nie wykonano zdalnego CI ani publikacji tego zakresu na main.
