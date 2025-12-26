# analizy-termiczne-dronowe
Wykorzystanie DJI Thermal SDK w praktyce OSP

## Instalacja
* Pobierz DJI Thermal SDK - https://www.dji.com/pl/downloads/softwares/dji-thermal-sdk i rozpakuj go.
* Biblioteki Python instalowane z apt-get (python3): scipy, numpy, matplotlib, tqdm,
* Biblioteka thermal-parser - pobierz z https://github.com/SanNianYiSi/thermal_parser a następnie ./setup.py install

## Konfiguracja

* W pliku termika.conf dostosuj parametry do swoich potrzeb. 
* Wskaż plik PNG z logo, nazwę jednostki. 
* W parametrze dji_libs_path wprowadź ścieżkę dostępu do katalogu z plikami .so (zobacz przykład w pliku termika.conf) - ponieważ oryginalnie thermal_parser używa SDK w wersji 1.7, musimy samodzielnie przygotować nowsze, obsługujące m.in. H30T.

## Praca
Umieść pliki termika.py i termika.conf w katalogu z kadrami do analizy. Następnie wywołaj przy pomocy
```
python3 ./termika.py
```
lub jeśli chcesz wyznaczyć obszary z temperaturą zbliżoną do mediany (zakresy definiujesz w termika.conf)
```
python3 ./termika.py -strefa
```
