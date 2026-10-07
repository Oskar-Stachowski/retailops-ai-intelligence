# Dostęp do modeli Anthropic w AWS

Formularz pierwszego użycia został wysłany 2026-09-29 po potwierdzeniu danych
przez właściciela: projekt osobisty, profil GitHub, użytkownicy wewnętrzni,
branża retail. Opis obejmuje rozwój asystenta tylko do odczytu, testy na danych
syntetycznych i dokumentacji oraz brak automatycznych działań biznesowych.

Aktywowano publiczne oferty Haiku 4.5 i Sonnet 4.6, z opłatami według użycia
zgodnymi z przypiętym cennikiem. Nie utworzono rezerwacji mocy obliczeniowej.
[Zapis aktywacji](evidence/12-bedrock-model-access.json) pokazuje przyjęcie
operacji; raporty smoke zawierają późniejszy odczyt bieżącej dostępności.
Nie przechowujemy formularza, poświadczeń ani tokenów oferty w repozytorium.

## Praca na innym koncie

1. Właściciel podaje prawdziwe dane w jednorazowym formularzu Anthropic,
   w katalogu modeli Bedrock lub przez `PutUseCaseForModelAccess`.
2. Uprawniona tożsamość aktywuje modele przez AWS Marketplace. Konto musi mieć
   poprawną metodę płatności. Nie rozszerzać IAM bez rozpoznania brakującego prawa.
3. Przed testem sprawdzić dostęp oraz pozostały budżet. CLI odrzuca niepełny lub
   nieznany status przed płatnymi wywołaniami i nie zmienia dostępu automatycznie.

Limit **1,00 USD łącznie** na obecną serię jest już zatwierdzony.
[Instrukcja testu i rozliczenie](agent-bedrock.md) opisują dalsze wykonanie.
[Wymagania AWS](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
dopuszczają portfolio, GitHub lub adres projektu dla osoby bez strony firmy.
