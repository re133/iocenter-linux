#!/usr/bin/env python3
"""
Zweisprachigkeit -- Deutsch und Englisch.

Bewusst ohne Qt-Übersetzungsdateien: keine .ts/.qm, kein lupdate/lrelease,
keine Build-Schritte. Die deutschen Texte sind die Schlüssel, Englisch steht
in der Tabelle darunter. Fehlt ein Eintrag, erscheint der deutsche Text --
die Oberfläche bleibt also in jedem Fall benutzbar.

    from bqi18n import tr
    label = QLabel(tr("Beleuchtung"))

Die Sprache kommt beim Start aus config.toml ([ui] language) und fällt
sonst auf die Umgebungsvariablen zurück.
"""

import os
import re

import bqconfig

LANGUAGES = {"de": "Deutsch", "en": "English"}

_current = "de"

ENGLISH = {
    # -- Fenster, Kopfzeile, Bereiche
    "IO Center für Linux": "IO Center for Linux",
    "Verbinde …": "Connecting …",
    "Verbunden über /dev/%s": "Connected via /dev/%s",
    "Keine Tastatur gefunden": "No keyboard found",
    "Kein Zugriff auf /dev/%s (%s)": "No access to /dev/%s (%s)",
    "Tastatur getrennt": "Keyboard disconnected",
    "Tasten": "Keys",
    "Media-Dock": "Media Dock",
    "Beleuchtung": "Lighting",
    "Web-App": "Web app",
    "Geräteinfo": "Device info",
    "Lese Geräteinformationen …": "Reading device information …",
    "Geräteinformationen gelesen.": "Device information read.",
    "Geräteinformationen nicht verfügbar: %s":
        "Device information unavailable: %s",
    "Modell": "Model",
    "Hardware-Revision": "Hardware revision",
    "Firmware": "Firmware",
    "Seriennummer": "Serial number",
    "Öffnet iocenter.bequiet.com — dort laufen Firmware-Updates.":
        "Opens iocenter.bequiet.com — firmware updates happen there.",
    "Bitte warten, bis der laufende Vorgang beendet ist.":
        "Please wait for the current operation to finish.",
    "Der Monitor wird noch beendet …": "The monitor is still stopping …",
    "Die Geräteverbindung wird noch beendet …":
        "The device connection is still closing …",

    # -- Einmaliger Sicherheitshinweis
    "Wichtiger Sicherheitshinweis": "Important safety notice",
    "INOFFIZIELLES COMMUNITY-PROJEKT": "UNOFFICIAL COMMUNITY PROJECT",
    "Bevor es losgeht": "Before you begin",
    "Diese Software kommuniziert direkt mit deiner Hardware. Bitte lies die "
    "folgenden Hinweise vollständig, bevor du fortfährst.":
        "This software communicates directly with your hardware. Please read "
        "the following information in full before continuing.",
    "Nutzung auf eigene Verantwortung": "Use at your own risk",
    "IO Center für Linux wird ohne Garantie oder Gewährleistung "
    "bereitgestellt. Du entscheidest selbst, die Software zu verwenden. "
    "Soweit gesetzlich zulässig, übernehmen die Mitwirkenden keine Haftung "
    "für Geräteschäden, Datenverlust, Fehlkonfigurationen, Ausfallzeiten oder "
    "sonstige direkte und indirekte Schäden.":
        "IO Center for Linux is provided without warranty of any kind. You "
        "decide whether to use the software. To the extent permitted by law, "
        "the contributors accept no liability for device damage, data loss, "
        "misconfiguration, downtime, or other direct or indirect damage.",
    "Unabhängig entwickelt": "Independently developed",
    "Dieses Projekt ist nicht mit be quiet! verbunden und wird nicht von be "
    "quiet! unterstützt. Die Gerätekommunikation wurde unabhängig für "
    "kompatible Dark-Mount-Hardware nachvollzogen. Andere Modelle oder "
    "zukünftige Firmware können sich anders verhalten.":
        "This project is not affiliated with or supported by be quiet! Device "
        "communication was independently studied for compatible Dark Mount "
        "hardware. Other models or future firmware may behave differently.",
    "Keine Firmware-Eingriffe": "No firmware operations",
    "Die Anwendung implementiert keine Firmware-Updates und keine Bootloader-"
    "Funktionen. Verwende für Firmware-Updates ausschließlich die offizielle "
    "Web-App von be quiet!. Ein vollständig risikofreier Hardwarezugriff kann "
    "trotzdem nicht zugesichert werden.":
        "The application does not implement firmware updates or bootloader "
        "functions. Only use the official be quiet! web app for firmware "
        "updates. Completely risk-free hardware access still cannot be "
        "guaranteed.",
    "Schreibvorgänge niemals unterbrechen": "Never interrupt write operations",
    "Trenne während des Übertragens von Bildern, Einstellungen oder "
    "Beleuchtungseffekten weder die Tastatur noch das Media-Dock. Beende die "
    "Anwendung nicht und schalte den Rechner nicht aus, solange ein "
    "Fortschrittsfenster sichtbar ist. Ein Abbruch kann Daten auf dem Gerät "
    "unvollständig hinterlassen.":
        "Do not disconnect the keyboard or Media Dock while transferring "
        "images, settings, or lighting effects. Do not close the application "
        "or shut down the computer while a progress window is visible. An "
        "interruption may leave data on the device incomplete.",
    "Berechtigungen, Befehle und Sicherungen":
        "Permissions, commands, and backups",
    "Die udev-Regel erlaubt deinem Benutzer den direkten Zugriff auf das "
    "Gerät und auf uinput. Hinterlegte Tastenbefehle laufen mit deinen "
    "Benutzerrechten — verwende deshalb nur vertrauenswürdige Kommandos und "
    "Dateien. Automatische Sicherungen können bei der Wiederherstellung "
    "helfen, sind aber keine Garantie für eine erfolgreiche Rettung.":
        "The udev rule grants your user direct access to the device and to "
        "uinput. Assigned key commands run with your user permissions, so "
        "only use trusted commands and files. Automatic backups can help "
        "with recovery, but do not guarantee a successful restore.",
    "Fragen oder etwas Auffälliges entdeckt?":
        "Questions or noticed something unusual?",
    "Discord-Namen kopieren und eine Nachricht senden.":
        "Copy the Discord username and send a message.",
    "Discord-Benutzernamen kopieren": "Copy Discord username",
    "Kopiert — in Discord unter „Freunde hinzufügen“ einfügen.":
        "Copied — paste it under ‘Add Friend’ in Discord.",
    "Ich habe die Hinweise gelesen und akzeptiere die Nutzung auf eigenes "
    "Risiko.":
        "I have read the information and accept using the software at my own "
        "risk.",
    "Beenden": "Quit",
    "Verstanden und fortfahren": "I understand — continue",
    "Schließen": "Close",

    # -- Hilfe, Kontakt und udev
    "Hilfe & Kontakt": "Help & contact",
    "Kontakt, Sicherheitshinweis und Geräte-Zugriff":
        "Contact, safety notice, and device access",
    "Systemzugriff prüfen, Hinweise nachlesen oder direkt Kontakt aufnehmen.":
        "Check system access, review safety information, or get in touch.",
    "Kontakt": "Contact",
    "Bei Fragen, Fehlern oder ungewöhnlichem Geräteverhalten erreichst du "
    "den Entwickler über Discord.":
        "For questions, bugs, or unusual device behaviour, contact the "
        "developer on Discord.",
    "Benutzernamen kopieren": "Copy username",
    "Kopiert: %s": "Copied: %s",
    "Version %s · %s": "Version %s · %s",
    "Geräte-Zugriff": "Device access",
    "Die App läuft ohne root. Eine eng begrenzte udev-Regel gibt dem aktiven "
    "Benutzer Zugriff auf Dark Mount und optional uinput.":
        "The app runs without root. A narrowly scoped udev rule grants the "
        "active user access to Dark Mount and optionally to uinput.",
    "Einrichtungsbefehl kopieren": "Copy setup command",
    "Kopiert die drei transparenten sudo-Befehle für das Terminal.":
        "Copies the three explicit sudo commands for the terminal.",
    "Status aktualisieren": "Refresh status",
    "Regel entfernen …": "Remove rule …",
    "udev-Regel entfernen": "Remove udev rule",
    "Den Entfernungsbefehl für die manuell installierte udev-Regel kopieren? "
    "Danach verliert die App beim nächsten Verbinden den Gerätezugriff.":
        "Copy the removal command for the manually installed udev rule? The "
        "app will lose device access the next time the keyboard is connected.",
    "Entfernungsbefehl kopiert. Er löscht nur die Projektregel unter /etc und "
    "lädt udev neu.":
        "Removal command copied. It only deletes the project rule under /etc "
        "and reloads udev.",
    "Nach der Einrichtung die Tastatur einmal trennen und wieder verbinden. "
    "AUR-, DEB- und RPM-Pakete installieren die Regel später automatisch.":
        "After setup, disconnect and reconnect the keyboard once. AUR, DEB, "
        "and RPM packages will install the rule automatically.",
    "Sicherheitshinweis anzeigen": "Show safety notice",
    "Lizenz anzeigen": "Show license",
    "Lizenz": "License",
    "Lizenz und Urheberrecht": "License and copyright",
    "Copyright © 2026 %s · Veröffentlicht unter %s":
        "Copyright © 2026 %s · Released under %s",
    "Der vollständige Lizenztext wurde nicht gefunden. Siehe %s":
        "The full license text was not found. See %s",
    "Befehl kopiert. Im Terminal einfügen und anschließend die Tastatur neu "
    "verbinden.":
        "Command copied. Paste it into a terminal, then reconnect the "
        "keyboard.",
    "Alles bereit": "Everything ready",
    "Grundfunktionen bereit": "Basic functions ready",
    "Berechtigung fehlt": "Permission missing",
    "Regel installiert": "Rule installed",
    "Einrichtung erforderlich": "Setup required",
    "✓ Projektregel installiert: %s": "✓ Project rule installed: %s",
    "✓ Zugriff funktioniert über eine andere lokale udev-Regel.":
        "✓ Access works through another local udev rule.",
    "○ Projektregel nicht installiert": "○ Project rule not installed",
    "○ Dark Mount nicht verbunden": "○ Dark Mount not connected",
    "✓ Dark Mount erkannt — HID-Zugriff funktioniert":
        "✓ Dark Mount detected — HID access works",
    "! Dark Mount erkannt — kein Zugriff auf %s":
        "! Dark Mount detected — no access to %s",
    "✓ Virtuelle F13–F24-Tasten über uinput verfügbar":
        "✓ Virtual F13–F24 keys available through uinput",
    "! uinput vorhanden, aber nicht beschreibbar":
        "! uinput exists but is not writable",
    "○ uinput nicht verfügbar — virtuelle Tasten sind optional":
        "○ uinput unavailable — virtual keys are optional",
    "Zugriff einrichten": "Set up access",
    "Später": "Later",
    "Gerätezugriff noch nicht eingerichtet": "Device access is not set up",
    "Dark Mount wurde erkannt, aber Linux blockiert den HID-Zugriff. Auch "
    "virtuelle Tasten benötigen noch Zugriff auf uinput.":
        "Dark Mount was detected, but Linux is blocking HID access. Virtual "
        "keys also still need access to uinput.",
    "Dark Mount wurde erkannt, aber Linux blockiert den direkten HID-Zugriff. "
    "Bilder, Einstellungen und Beleuchtung funktionieren erst nach der "
    "Einrichtung.":
        "Dark Mount was detected, but Linux is blocking direct HID access. "
        "Images, settings, and lighting will work after access is set up.",
    "Virtuelle Tasten benötigen eine Berechtigung":
        "Virtual keys need permission",
    "Der Gerätezugriff funktioniert, aber Linux blockiert /dev/uinput. "
    "F13–F24 können erst nach der Einrichtung erzeugt werden.":
        "Device access works, but Linux is blocking /dev/uinput. F13–F24 can "
        "be generated after access is set up.",

    # -- Dienst
    "Autostart einrichten": "Enable autostart",
    "Autostart aktivieren": "Enable autostart",
    "Autostart entfernen": "Remove autostart",
    "Den Tastendienst anhalten und aus dem Autostart entfernen?":
        "Stop the key service and remove it from autostart?",
    "Autostart entfernt.": "Autostart removed.",
    "systemctl wurde nicht gefunden.": "systemctl was not found.",
    "Der systemd-Userdienst ist nicht verfügbar: %s":
        "The systemd user service is unavailable: %s",
    "Dienst starten": "Start service",
    "Dienst anhalten": "Stop service",
    "Dienst läuft": "Service running",
    "Dienst angehalten": "Service stopped",
    "Tasten wirken nur bei offenem Fenster":
        "Keys work only while this window is open",
    "Autostart eingerichtet — die Tasten wirken jetzt dauerhaft.":
        "Autostart enabled — the keys now work permanently.",
    "Gespeichert — Dienst neu gestartet.": "Saved — service restarted.",
    "Autostart": "Autostart",

    # -- Seite: Tasten
    "Display Keys": "Display keys",
    "Drücke eine Taste — die zugehörige Kachel leuchtet auf. Zum Belegen "
    "anklicken.":
        "Press a key — its tile lights up. Click a tile to assign it.",
    "Tastenbilder laden": "Load key images",
    "Liest die Bilder von der Tastatur (nur lesend, 0x20 0x03).":
        "Reads the images from the keyboard (read-only, 0x20 0x03).",
    "Keine Taste ausgewählt": "No key selected",
    "Taste %d": "Key %d",
    "Bild ändern …": "Change image …",
    "Bild dieser Taste ersetzen. Das bisherige wird gesichert und das neue "
    "nach dem Schreiben zurückgelesen und geprüft.":
        "Replace this key's image. The previous one is backed up, and the new "
        "one is read back and verified after writing.",
    "Name": "Name",
    "z. B. Screenshot": "e.g. screenshot",
    "Virtuelle Taste": "Virtual key",
    "Erzeugt beim Druck eine virtuelle Taste. Die lässt sich dann in den "
    "Systemeinstellungen unter Kurzbefehle aufzeichnen.":
        "Emits a virtual key press, which can then be recorded in the system "
        "settings under Shortcuts.",
    "Kommando": "Command",
    "optional, z. B. spectacle --region": "optional, e.g. spectacle --region",
    "Anwendung …": "Application …",
    "Datei …": "File …",
    "Übernehmen": "Apply",
    "Belegung löschen": "Clear assignment",
    "nicht belegt": "not assigned",
    "(keine)": "(none)",
    "Virtuelle Taste und Kommando sind beide belegt — beim Druck passiert "
    "beides. Für „nur Programm starten“ die virtuelle Taste auf „(keine)“ "
    "setzen.":
        "Both a virtual key and a command are set — pressing does both. For "
        "“just launch a program”, set the virtual key to “(none)”.",
    "Belegung gespeichert.": "Assignment saved.",
    "Speichern fehlgeschlagen": "Saving failed",
    "Lese Bild von Taste %d …": "Reading image from key %d …",
    "%d Tastenbilder geladen.": "Loaded %d key images.",
    "%d von 8 Bildern geladen — %s": "Loaded %d of 8 images — %s",
    "Sichere bisheriges Bild von Taste %d …":
        "Backing up previous image of key %d …",
    "Schreibe auf Taste %d …": "Writing to key %d …",
    "Prüfe durch Zurücklesen …": "Verifying by reading back …",
    "Taste %d geändert und geprüft.": "Key %d changed and verified.",
    "Abweichung beim Zurücklesen. Sicherung: %s":
        "Mismatch when reading back. Backup: %s",
    "Bild schreiben": "Write image",
    "Taste %d gedrückt": "Key %d pressed",
    "Taste %d  →  %s": "Key %d  →  %s",
    "Taste %d — nicht belegt": "Key %d — not assigned",
    "Virtuelle Taste nicht möglich: %s": "Virtual key not possible: %s",
    "Start fehlgeschlagen: %s": "Launch failed: %s",
    "Gerät nicht verfügbar: %s": "Device not available: %s",
    "Fehlgeschlagen: %s": "Failed: %s",

    # -- Anwendungsauswahl
    "Anwendung auswählen": "Select application",
    "Suchen…": "Search…",
    "Programm oder Skript auswählen": "Select program or script",

    # -- Bilddialog
    "Bild für Taste %d": "Image for key %d",
    "Noch kein Bild gewählt": "No image selected yet",
    "So erscheint das Bild auf der Taste (120 × 120)":
        "This is how the image appears on the key (120 × 120)",
    "Bilddatei wählen …": "Choose image file …",
    "App-Icon auswählen …": "Choose app icon …",
    "App-Icon auswählen": "Choose app icon",
    "Icon-Galerie": "Icon gallery",
    "Installierte Anwendungen werden direkt angezeigt. Die Suche findet "
    "zusätzlich Symbole aus deinen lokalen Icon-Paketen.":
        "Installed applications appear directly. Search also finds symbols "
        "from your local icon packs.",
    "Apps und Symbole suchen …": "Search apps and symbols …",
    "%d Icons gefunden": "%d icons found",
    "Keine passenden Icons gefunden": "No matching icons found",
    "Icon kann nicht gelesen werden.": "Icon cannot be read.",
    "Ausschnitt": "Crop",
    "Auf Taste schreiben": "Write to key",
    "Bild wählen": "Choose image",
    "Bilder (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;Alle Dateien (*)":
        "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*)",
    "Bild kann nicht gelesen werden: %s": "Image cannot be read: %s",
    "%d Byte bei Qualität %d — Grenze %d Byte":
        "%d bytes at quality %d — limit %d bytes",

    # -- Seite: Media-Dock
    "Das Modul mit eigenem Display, 320 × 240. Die Menüfarbe gilt für die "
    "Ansichten auf dem Gerät.":
        "The module with its own 320 × 240 display. The menu colour applies "
        "to the views shown on the device.",
    "Kein Bild gewählt": "No image selected",
    "Leerlaufgrafik, 320 × 240": "Idle image, 320 × 240",
    "Bild wählen …": "Choose image …",
    "Vom Dock lesen": "Read from dock",
    "Auf das Dock schreiben": "Write to the dock",
    "Vor dem Schreiben wird das aktuelle Bild gesichert; danach wird das neue "
    "zurückgelesen und geprüft. Es landet im Flash — häufiges Überschreiben "
    "nutzt ihn ab.":
        "Before writing, the current image is backed up; the new image is then "
        "read back and verified. It is stored in flash — frequent rewriting "
        "wears it out.",
    "Menüfarbe": "Menu colour",
    "Anzeige": "Display",
    "Uhr": "Clock",
    "Bild": "Image",
    "Uhrzeit": "Time",
    "24 Stunden": "24 hours",
    "12 Stunden": "12 hours",
    "Leerlaufgrafik nach": "Idle image after",
    "Display ausschalten": "Turn display off",
    "nach": "after",
    "Vom Gerät lesen": "Read from device",
    "Einstellungen übernehmen": "Apply settings",
    "Einstellungen gelesen.": "Settings read.",
    "Einstellungen übernommen.": "Settings applied.",
    "Dock nicht erreichbar: %s": "Dock not reachable: %s",
    "Bild für das Dock wählen": "Choose image for the dock",
    "Vorschau zeigt exakt die Farben des Displays (RGB565).":
        "The preview shows exactly the colours of the display (RGB565).",
    "Anzeige umschalten?": "Switch display?",
    "Das Dock zeigt gerade die Uhr. Soll nach dem Übertragen auf „Bild“ "
    "umgeschaltet werden?":
        "The dock is currently showing the clock. Switch to “Image” after "
        "transferring?",
    "Leerlaufgrafik übertragen.": "Idle image transferred.",
    "Leerlaufgrafik gesichert, übertragen und geprüft. Sicherung: %s":
        "Idle image backed up, transferred and verified. Backup: %s",
    " Sicherung: %s": " Backup: %s",
    "Leerlaufgrafik vom Dock gelesen.": "Idle image read from the dock.",
    "Lese Leerlaufgrafik vom Dock …": "Reading idle image from the dock …",

    # -- Seite: Beleuchtung
    "Die Effekte laufen im Gerät selbst — sie bleiben aktiv, auch wenn der "
    "Rechner aus ist.":
        "The effects run on the device itself — they stay active even when "
        "the computer is off.",
    "Aktiver Modus nicht ausgelesen": "Active mode not read",
    "Farbe": "Colour",
    "Helligkeit": "Brightness",
    "Tempo": "Speed",
    "Je nach Effekt verwendet die Tastatur eine Farbe, zwei Farben oder die "
    "gesamte Palette.":
        "Depending on the effect, the keyboard uses one colour, two colours, "
        "or the entire palette.",
    "Auf die Tastatur anwenden": "Apply to the keyboard",
    "Farbe der Beleuchtung": "Lighting colour",
    "%s gesetzt.": "%s applied.",
    "Statisch": "Static",
    "Farbwelle": "Colour wave",
    "Tornado": "Tornado",
    "Atmen": "Breathing",
    "Reaktiv": "Reactive",
    "Matrix": "Matrix",

    'Farben': 'Colours',
    'Farbe hinzufügen': 'Add colour',
    'Letzte entfernen': 'Remove last',
    'zum Ändern klicken': 'click to change',
    'Eine Farbe': 'One colour',
    'Zwei Farben': 'Two colours',
    'Palette mit %d Farben': 'Palette of %d colours',
    'Per-Key-RGB': 'Per-key RGB',
    'Uhr stellen': 'Set clock',
    'Uhr des Docks auf die Systemzeit gestellt.': 'Dock clock set to system time.',

    'Einzelne Tasten': 'Individual keys',
    'Über den offenen HID-LampArray-Standard, vom Rechner gerechnet. Endet, sobald die Steuerung zurückgegeben wird.': 'Via the open HID LampArray standard, computed on the PC. Ends as soon as control is handed back.',
    'Einfarbig': 'Solid',
    'Verlauf': 'Gradient',
    'Steuerung zurückgeben': 'Hand back control',
    'Steuerung an das Gerät zurückgegeben.': 'Control handed back to the device.',
    '%d Lampen gesetzt.': '%d lamps set.',
    'PC-Beleuchtung': 'PC lighting',
    'Temporär vom Rechner gesteuert und nicht in den Flash geschrieben. Beim Wiederherstellen kehrt der Onboard-Effekt zurück.':
        'Temporarily controlled by the computer and not written to flash. Restoring returns to the onboard effect.',
    'Zweite Farbe': 'Second colour',
    'Einfarbig anzeigen': 'Show solid colour',
    'Verlauf anzeigen': 'Show gradient',
    'Onboard-Effekt wiederherstellen': 'Restore onboard effect',
    'Farbe der PC-Beleuchtung': 'PC lighting colour',
    'PC · Auslastungsanzeige': 'PC · Load meter',
    'PC · Einfarbig': 'PC · Solid',
    'PC · Verlauf': 'PC · Gradient',
    'Onboard-Effekt aktiv': 'Onboard effect active',
    'Onboard · %s': 'Onboard · %s',
    'Auslastungsanzeige': 'Load meter',
    'F1–F10 zeigen GPU 10–100 %, die Tasten 1–0 CPU 10–100 %. Nur LampArray im RAM; beim Stoppen kehrt der Onboard-Effekt zurück.':
        'F1–F10 show GPU 10–100%, and keys 1–0 show CPU 10–100%. LampArray in RAM only; stopping restores the onboard effect.',
    'Nur aktuelle Stufe': 'Current level only',
    'Balken': 'Bar',
    'Reihen tauschen': 'Swap rows',
    'GPU auf 1–0 und CPU auf F1–F10 anzeigen.':
        'Show GPU on 1–0 and CPU on F1–F10.',
    'Monitor starten': 'Start monitor',
    'Monitor stoppen': 'Stop monitor',
    'Starte GPU-/CPU-Monitor …': 'Starting GPU/CPU monitor …',
    'Monitor aktiv über %s; GPU-Quelle: %s':
        'Monitor active via %s; GPU source: %s',
    'Monitor beendet; Onboard-Beleuchtung wieder aktiv.':
        'Monitor stopped; onboard lighting restored.',
    'Monitor fehlgeschlagen: %s': 'Monitor failed: %s',
    'Bitte laufende Beleuchtungsoperation abwarten.':
        'Please wait for the current lighting operation to finish.',
    'Die Uhr des Docks läuft ohne Abgleich mit der Zeit davon.': "Without syncing, the dock's clock drifts over time.",

    'Leerlaufanzeige': 'Idle display',
    'Die Leerlaufanzeige erscheint erst nach der eingestellten Wartezeit ohne Bedienung — nicht sofort. Zum Ausprobieren die Wartezeit kurz auf wenige Sekunden stellen.': 'The idle display appears only after the configured delay without input — not immediately. To try it out, set the delay to a few seconds.',

    'Lese Tastenbilder …': 'Reading key images …',
    'Schreibe Bild auf Taste %d …': 'Writing image to key %d …',
    'Lese Einstellungen …': 'Reading settings …',
    'Übertrage Einstellungen …': 'Transferring settings …',
    'Stelle die Uhr …': 'Setting the clock …',
    'Sichere, übertrage und prüfe die Leerlaufgrafik …':
        'Backing up, transferring, and verifying the idle image …',
    'Setze Effekt …': 'Applying effect …',
    'Spreche die Lampen an …': 'Addressing the lamps …',

    'Uhr auf %s gestellt, vom Gerät bestätigt.': 'Clock set to %s, confirmed by the device.',
    'Uhr auf %s gestellt — keine Bestätigung erhalten.': 'Clock set to %s — no confirmation received.',
    'Uhr auf %s gestellt, Gerät meldet %s.': 'Clock set to %s, device reports %s.',
    'Sobald ein Rechner mit dem Dock spricht, zeigt es sein Menü. Uhr und Bild erscheinen erst wieder als Leerlaufanzeige, nach der eingestellten Wartezeit ohne Bedienung. Zum Ausprobieren die Wartezeit kurz auf wenige Sekunden stellen.': 'As soon as a computer talks to the dock, it shows its menu. Clock and image only reappear as the idle display, after the configured delay without input. To try it out, set the delay to a few seconds.',

    # -- Sprache
    "Sprache": "Language",
    "Die Sprache wird beim Neustart der Anwendung übernommen.":
        "The language takes effect when the application restarts.",
    "Sprache umstellen": "Change language",
    "Die Anwendung startet neu, um die Sprache zu übernehmen. Fortfahren?":
        "The application will restart to apply the language. Continue?",
}


def detect_language():
    """Sprache aus config.toml, sonst aus der Umgebung."""
    language = _from_config()
    if language in LANGUAGES:
        return language
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(variable, "")
        if value:
            code = value.split(".")[0].split("_")[0].lower()
            if code in LANGUAGES:
                return code
            # Alles, was nicht Deutsch ist, bekommt Englisch.
            if code and code != "de":
                return "en"
    return "de"


def _config_path():
    import bqkeyd
    return bqkeyd.DEFAULT_CONFIG


def _from_config():
    try:
        with open(_config_path(), "rb") as fh:
            import tomllib
            return tomllib.load(fh).get("ui", {}).get("language")
    except (OSError, ValueError):
        return None


def save_language(language):
    """Schreibt [ui] language in die Konfiguration, ohne den Rest anzurühren."""
    path = _config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        text = ""

    line = 'language = "%s"' % language
    match = re.search(r"(?ms)^\[ui\][^\n]*\n.*?(?=^\[|\Z)", text)
    if match:
        section = match.group(0).rstrip()
        if re.search(r"^language\s*=", section, re.MULTILINE):
            section = re.sub(r"^language\s*=.*$", line, section, count=1,
                             flags=re.MULTILINE)
        else:
            section += "\n" + line
        text = text[:match.start()] + section + "\n" + text[match.end():]
    else:
        text = text.rstrip("\n") + "\n\n[ui]\n" + line + "\n"

    try:
        bqconfig.atomic_write_text(path, text)
    except OSError:
        return False
    return True


def set_language(language):
    global _current
    _current = language if language in LANGUAGES else "de"
    return _current


def language():
    return _current


def tr(text):
    """Übersetzt einen deutschen Text; unbekannte bleiben unverändert."""
    if _current == "de":
        return text
    return ENGLISH.get(text, text)


set_language(detect_language())
