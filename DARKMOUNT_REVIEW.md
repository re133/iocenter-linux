# Vergleich mit `darkmount-linux-main.zip`

Quelle des Vergleichsarchivs:
[`fbnlrz/darkmount-linux`](https://github.com/fbnlrz/darkmount-linux)

Geprüfter Archivstand: Commit `a4b11156ddf79c4ca1eeea380d3f3d9093231881`,
Lizenz **GPL-3.0-only**.

Das Archiv ist eine eigenständige Rust/Tauri-Anwendung. Dieses Projekt bleibt
bei Python/PyQt und übernimmt keinen Quelltext aus dem GPL-Projekt. Verwendet
wurden nur Protokollhinweise, die mit dem vorhandenen IOCenter-Mitschnitt, dem
HID-Deskriptor oder den mitgelieferten Firmware-Notizen abgeglichen wurden; die
Implementierung hier ist eigenständig.

## Übernommen

| Fund im Archiv | Integration hier | Begründung |
|---|---|---|
| Media-Dock `GetImage` (`21 06`) | `Dock.read_image()`, CLI `--read-image`, GUI „Vom Dock lesen“ | Reines Lesen; fester Bildtyp, exakte Größe 320×240 RGB565 und harte Antwortgrenzen |
| Geräteinformationen | `bqdevice.py` und GUI-Dialog | `03 01`/`03 02` sind auch im eigenen WebHID-Mitschnitt vorhanden; nur lesend |
| Explizite QLink-Sitzung | `bqlight.Lighting` öffnet mit `01 01` und schließt mit `01 02` | Der alte Heartbeat-Fallback wurde bei `10 06` mit Status 9 abgelehnt; Session-Position, Nonce-Echo und Status 0 wurden am Gerät geprüft |

Die Sitzungslogik steckt inzwischen zentral in `bqlink.py` und wird von
Geräteinfo, Tastenbildern, Dock, Beleuchtung und dem Diagnose-Prober geteilt.
CRC, Sequenz, Antwortkommando und Gerätestatus werden damit an einer Stelle
geprüft; die Fachmodule behalten ihre jeweils enge Nutzkommando-Whitelist.

Beide Integrationen wurden am 11. August 2026 an der angeschlossenen Dark
Mount mit MCU-Firmware 1.29.0 geprüft. Das Dock-Readback lieferte ein gültiges
PNG mit 320×240 Pixeln; dabei wurde nichts auf das Gerät geschrieben.

## Bereits besser oder gleichwertig vorhanden

| Darkmount | Dieses Projekt |
|---|---|
| Numpad-Bilder lesen/schreiben | Bereits mit Pflicht-Backup und byteweisem Zurücklesen umgesetzt |
| Dock-Konfiguration und Uhr | Bereits umgesetzt; Zeitübernahme wird über die Gerätebestätigung geprüft |
| Dock-Bild schreiben | Pflicht-Backup, prozessübergreifende Verschleißpause und byteweises Zurücklesen nach dem Schreiben |
| Sechs Onboard-Lichteffekte | Payloads waren bereits umgesetzt, inklusive Mehrfarben; der Session-Transport wurde aus dem Vergleich übernommen und eigenständig verifiziert |
| LampArray | Bereits über den offenen HID-Standard, mit vom Gerät gelesenen Lampenpositionen |
| Linux-Aktionen für Display Keys | Bereits passiv über Vendor-Events plus optionales `uinput`; keine session-gebundene Gerätebelegung nötig |
| Autostart | Bereits als systemd-User-Dienst für den Tastendaemon |

## Bewusst nicht übernommen

| Funktion | Grund |
|---|---|
| Now Playing auf dem Media-Dock | Jeder Titelwechsel überträgt 153.609 Byte in den Screensaver-Speicher. Das passt nicht zur Verschleißregel dieses Projekts. |
| Game-Mode, Win-Lock und Layout-Schreiben | Im Archiv selbst als plausibel, aber nicht am Gerät verifiziert markiert; das Layout wird sogar als ungerahmter HID-Write ohne Bestätigung gesendet. |
| Firmware/DFU | Bleibt ausdrücklich der offiziellen IOCenter-Web-App vorbehalten. |
| Direkte Rust-/React-/CSS-Übernahme | GPL-3.0-only; ohne geklärte Lizenz dieses Projekts wird kein Fremdcode kopiert. |

## Sinnvolle nächste Kandidaten

1. Profil-Export/-Import für Dock-Einstellungen, Tastenbelegung und
   Beleuchtung — lokal und ohne neue Gerätekommandos.
2. Eine interaktive LampArray-Karte für echte Einzeltasten-Auswahl.
