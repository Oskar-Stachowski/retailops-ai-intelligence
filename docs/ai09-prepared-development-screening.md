# Przekazanie przygotowanych danych do pierwszych prób

`PreparedDevelopmentParents` wiąże zakończone przygotowanie ordinary, demand
i physical z oryginalnymi dziennikami. Ordinary pozostaje generacją bootstrap,
a jego późniejszy replay pozostaje odczytem. Dla wariantów zachowane są właściwe
receipts generacji. Przekazanie nie dopisuje fikcyjnych generacji do nowego
dziennika ani nie zeruje wcześniejszych kosztów.

`capture_prepared_development_parents` sprawdza receipts na dysku, zakończenia
wszystkich trzech operacji przygotowania oraz niezmienność głowy dziennika.
Zachowuje zmierzone koszty generacji, planowania, reuse i obu wariantów.
`verify_prepared_development_parents` odtwarza te powiązania. Obie funkcje czytają
metadane audytu; nie otwierają faktów Source i nie przyznają dostępu do danych.

`compile_prepared_development_screening` tworzy osobny, niezmienny protokół
pierwszego porównania na pełnym profilu 25 produktów. Protokół obejmuje:

- trzy rozliczane eksporty oryginalnych rodziców, ze wspólnymi pięcioma rolami
  czasowymi, cechami, dostępnością i limitami populacji;
- po dwie różne, wcześniej określone konfiguracje RF, HGB i TensorFlow,
  trenowane na pełnej właściwej populacji ordinary;
- dwa kompletne porównania RF/HGB/TF i natywnych baseline'ów na roli tune,
  z identyczną populacją i budżetem odczytu.

Wszystkie jedenaście operacji i sześć prób fit jest zamrożonych przed pierwszym
eksportem. Cały plan poszukiwania zachowuje limit 12 fitów: 6 przy 25 produktach,
3 przy 50 i 3 przy 100. W większych profilach pozostaje ta sama, pierwotna
konfiguracja wybrana dla każdej rodziny. Nieudana próba zużywa slot; nie ma
automatycznego ponawiania. Każde
porównanie czeka na ukończenie właściwych trzech fitów. Pozostają oryginalne
wykonawcy eksportu, treningu z preprocessingiem, reload i scoringu.

Eksporter rozpoznaje nowy protokół i sprawdza oryginalne dzienniki przed
odczytem snapshot/curated oraz ponownie przed publikacją receiptu. Późniejszy
fit również sprawdza pochodzenie eksportu. Brak oryginalnego receiptu lub
zmieniony koszt/głowa dziennika blokuje wykonanie; wcześniejsza rezerwacja
i koszt nie znikają. Dotychczasowe protokoły nadal wymagają własnej zakończonej
generacji. Nie przyjmują po cichu danych z innego dziennika.

Nowy protokół nie potrzebuje identyfikatora nieistniejącej przyszłej kampanii
final. Zachowuje oryginalne polityki, lineage i legacy z przygotowania. Nie
zezwala jednak na awans do 50/100 produktów, kalibrację, final ani promocję.
Awans wymaga jeszcze wykonawcy, który odtworzy rzeczywiste wyniki tune,
sprawdzi pokrycie i bias, wybierze po jednej pierwotnej konfiguracji każdej
rodziny i przeniesie pełne koszty do kolejnego zamrożonego etapu. Nie zastępuje
to końcowej oceny wszystkich trzech zastosowań.

[Dowód 09.96](evidence/09-96-prepared-development-screening.json) obejmuje
**130 testów ze źródeł i te same 130 z zainstalowanej paczki**, bez pominięć.
Obejmują nową ścieżkę oraz istniejące warianty, eksport, fit, scoring i replay
dziennika. Kontrole używają rzeczywistych dzienników i prywatnych receipts,
ale kontrolowanych workerów. Nie stanowią wyniku przygotowania danych Project,
treningu ani kwalifikacji pełnej skali.

Pełny Mypy sprawdził 766 plików. Wszystkie 637 modułów Python i trzy schematy
v33 są identyczne w źródłach, wheelu i instalacji; 369 wcześniejszych
kontraktów zachowuje dotychczasowe bajty. Początkowe błędy dwóch fixtures,
cztery błędy typów i odmowy zasobów pozostają w evidence. Pełny Mypy
przekroczył początkowo limit 384 MiB na zimnym cache; ponowne sprawdzenie
z normalnie walidowanym cache rodzica przeszło przy tym samym limicie.
Pełny lokalny CI tego przyrostu oraz odbiór jego PR i wynikowego main pozostają
wymagane. Wcześniejszy PR 66 przeszedł 17/17 i został scalony; nie obejmował
jeszcze kodu przekazania do pierwszych prób.
