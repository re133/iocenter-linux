# Release-Ablauf

## 1. Version und Metadaten

Die Version an diesen Stellen gemeinsam aktualisieren:

- `bqmeta.py`
- `data/io.github.re133.iocenterlinux.metainfo.xml`
- `packaging/arch/PKGBUILD`
- `packaging/fedora/iocenter-linux.spec`
- `debian/changelog`
- `CHANGELOG.md`

## 2. Vollständige Prüfung

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v
python3 -m compileall -q -f .
desktop-file-validate data/io.github.re133.iocenterlinux.desktop
appstreamcli validate --no-net data/io.github.re133.iocenterlinux.metainfo.xml
udevadm verify 70-iocenter-dark-mount.rules
rm -rf build dist iocenter_linux.egg-info
python3 -m build
```

Der Build muss aus einem sauberen Arbeitsbaum erfolgen. Vor dem Upload den
Inhalt von Wheel und Source-Archiv prüfen; Cache-Dateien, lokale Konfiguration,
Geräte-Backups, die Vergleichs-ZIP und `bqprobe.py` gehören nicht ins Wheel.

Danach mindestens Bilder lesen, ein Testbild mit Sicherung schreiben, die
Dock-Einstellungen lesen, einen temporären LampArray-Modus starten und wieder
freigeben sowie Autostart aktivieren und entfernen.

## 3. Tag und Pakete

1. Signierten Tag `vX.Y.Z` erzeugen und zu GitHub übertragen.
2. GitHub-Release aus `CHANGELOG.md` erstellen und Wheel sowie Source-Archiv
   anhängen.
3. In `packaging/arch/PKGBUILD` `sha256sums=('SKIP')` mit `updpkgsums` durch
   die echte Prüfsumme des unveränderlichen Release-Archivs ersetzen.
4. AUR-, DEB- und RPM-Pakete bauen und jeweils Installation, Startmenü,
   udev-Regel, Autostart und Deinstallation prüfen.
5. Paketnutzer müssen die Tastatur nach der Installation einmal neu verbinden.

Die udev-Regel wird von nativen Paketen installiert und beim Entfernen des
Pakets wieder entfernt. Benutzerkonfiguration und Sicherungen bleiben bei
einer Deinstallation bewusst erhalten.
