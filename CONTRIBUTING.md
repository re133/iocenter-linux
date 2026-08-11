# Mitwirken

Danke für dein Interesse an IO Center für Linux. Fehlerberichte, getestete
Geräteinformationen, Übersetzungen und kleine, gut belegte Verbesserungen sind
willkommen.

## Sicherheitsgrenze

Gerätekommandos werden nur aufgenommen, wenn sie durch mindestens eine
nachvollziehbare Quelle belegt und anschließend am Gerät mit engen Grenzen
verifiziert wurden. Unbekannte Kommandos, Firmware-/DFU-Funktionen und
Bootloader-Eingriffe gehören nicht in dieses Projekt. Eine vermutete Bedeutung
ist kein ausreichender Beleg.

Jedes schreibende Gerätekommando benötigt:

- eine explizite Positivliste,
- harte Grenzen für IDs, Offsets und Nutzlastgrößen,
- eine Sicherung, sofern bestehende Benutzerdaten ersetzt werden,
- Rücklesen und Verifikation, soweit das Protokoll dies erlaubt,
- einen Test für ungültige oder abgeschnittene Antworten.

## Lokale Prüfung

```bash
python3 -m pip install --user -r requirements.txt
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v
python3 -m compileall -q -f .
desktop-file-validate data/io.github.re133.iocenterlinux.desktop
appstreamcli validate --no-net data/io.github.re133.iocenterlinux.metainfo.xml
udevadm verify 70-iocenter-dark-mount.rules
```

Hardwaretests bitte mit Modell, Firmware-Version, Distribution, Desktop und
genauem Ergebnis dokumentieren. Seriennummern und persönliche Tastenkommandos
nicht in Issues oder Logs veröffentlichen.

## Stil

- Kleine, überprüfbare Änderungen bevorzugen.
- Deutsch ist die Quellsprache der Oberfläche; neue sichtbare Texte brauchen
  einen englischen Eintrag in `bqi18n.py`.
- Keine festen `hidraw`-Nummern oder Home-Verzeichnisse verwenden.
- Benutzerdaten gehören ausschließlich in die XDG-Verzeichnisse.
