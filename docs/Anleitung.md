# AVerMedia Live Gamer Ultra 2.1 unter Linux

[English](Guide.md) · [Projekt-README](../README.md)

Die GC553G2 (`07ca:2553`) verwendet die Linux-Treiber `uvcvideo` und
`snd-usb-audio`. Die CLI benötigt Python 3.8 oder neuer, libusb 1.0 und
`v4l2-ctl`. Sie findet die Karte über sysfs anhand ihrer USB-ID und ermittelt
den passenden V4L2-Knoten dynamisch. Die GPU spielt für diese CLI keine Rolle.

Das Repository klonen oder als ZIP entpacken und in das Hauptverzeichnis wechseln.
Die Beispiele funktionieren in Fish und Bash.

```sh
python3 gc553g2.py devices
python3 gc553g2.py formats
python3 gc553g2.py capture --width 3840 --height 2160 --format MJPEG --fps 120 --test-frames 240
```

Bei mehreren Karten den Wert `usb_port` aus `devices` vor dem Unterbefehl angeben,
zum Beispiel `python3 gc553g2.py --port 2-1 formats`. Für den Aufnahmetest
Aufnahmeprogramme schließen. Der Test liest Bildpuffer ohne Datei oder
Dekodierung. `devices`, `formats` und `capture` melden die ausgehandelte
Geschwindigkeit in `speed_mbps`. `usb_10gbps_or_faster` zeigt an, ob der
aktuelle USB-Link mindestens 10 Gbit/s erreicht. Die CLI liest die verfügbaren
Aufnahmemodi unabhängig davon über V4L2 aus. 10 Gbit/s bilden die Basis
für die 4K120/144-Tests. 4K120 wurde bei 10 Gbit/s geprüft.
Die Windows-Tabelle von AVerMedia führt 4K120/144 in MJPEG bei 10 Gbit/s
und bei 5 Gbit/s maximal 4K60 in MJPEG auf.
[AVerMedia-Tabelle nach Videoformat](https://www.avermedia.com/de/files/large/5eb4cd6e1570f4d)

Für die folgenden Befehle Aufnahmeprogramme vorher schließen. Die CLI löst
die Bindung an den Videotreiber kurzzeitig. `enable` und `disable` können eine
USB-Neuanmeldung auslösen.

```sh
sudo python3 gc553g2.py status
sudo python3 gc553g2.py hdmi-status
sudo python3 gc553g2.py edid merge
sudo python3 gc553g2.py hdmi-preset
sudo python3 gc553g2.py edid-table
sudo python3 gc553g2.py edid-live
sudo python3 gc553g2.py enable
sudo python3 gc553g2.py disable
```

Wenn `formats` für 4K MJPEG nur 60 fps meldet, aktiviert `enable` den
erweiterten Aufnahmemodus und prüft anschließend die V4L2-Liste auf
120 und 144 fps. HDMI-Passthrough und die USB-Aufnahme sind getrennte
Pfade. Ob die HDMI-Quelle ein passendes Signal ausgibt, zeigt `hdmi-status`.

Die Profil- und EDID-Umschaltung sind auf Firmware 1.0.10.2 begrenzt.
`hdmi-status` liest den HDMI-Eingang und den EDID-Modus.
`edid` akzeptiert `merge` (gemeinsame Modi von Karte und Monitor),
`copy` (EDID des Monitors) und `internal` (EDID der Karte). Ein Wechsel
kann das HDMI-Signal kurz unterbrechen. Die CLI bestätigt den Wechsel
anhand des zurückgelesenen Modus. Merge und Internal wurden in beide
Richtungen an der Karte geprüft. `hdmi-preset` liest den Gerätecode der
voreingestellten HDMI-EDID-Tabelle. Mit `hdmi-preset 2.1-144`,
`hdmi-preset 2.1-120` oder `hdmi-preset 2.0` kann die Tabelle auf Firmware
1.0.10.2 gewechselt werden. Die Karte startet dabei neu und die CLI prüft
den neuen Code nach der USB-Neuanmeldung. Der Wechsel zwischen 2.1-144
und 2.1-120 wurde in beide Richtungen an der Karte geprüft. Die Rückkehr
von der HDMI-2.0-Testfolge zu 2.1-144 ist bestätigt. Code 2 wurde dabei
noch nicht separat zurückgelesen.
`edid-table` liest die intern gespeicherte EDID des jeweiligen HDMI-Presets
einschließlich der Erweiterungsblöcke. Auf der Karte wurden die Basis- und
CTA-Timings des Presets `2.1-144` geprüft.
`edid-backup` liest alle sieben gespeicherten Preset-Slots einschließlich
ihres reservierten Bereichs und gibt die Daten samt SHA-256 als JSON aus.
Die vollständige Sicherung wurde auf einer GC553G2 mit Firmware 1.0.10.2
geprüft. Alle sieben Slots enthielten je zwei gültige EDID-Blöcke.
`edid-live` fragt die EDID am HDMI-Eingang der Karte ab und zeigt ihre
Timings. Auf der Testkarte lieferte Merge eine vom gespeicherten
Preset abweichende EDID, deren erster CTA-Modus bereits 4K120 war.

Eine vorhandene Auflösung und Bildrate aus den detaillierten Timings
lässt sich offline als bevorzugtes Timing im EDID-Basisblock vorbereiten.
Der Befehl arbeitet mit der Sicherungsdatei und gibt den Entwurf als JSON aus.

```sh
python3 gc553g2.py edid-prefer-preview gc553g2-edid-backup.json --width 2560 --height 1440 --hz 120
python3 gc553g2.py edid-prefer-preview gc553g2-edid-backup.json --vic 118
```

Im zweiten Beispiel wird 4K120 (VIC 118) als erster CTA-Modus eingetragen.
Das bevorzugte Timing im Basisblock bleibt bei dieser CTA-Variante erhalten.
Für einen Hardwaretest der temporären Empfänger-EDID kann
`edid-live-test` eine bestehende Auflösung bevorzugen und danach die
ursprüngliche Tabelle wiederherstellen. Der Befehl benötigt Firmware
1.0.10.2, eine ausgeschaltete HDMI-Quelle und einen neuen Pfad für die
binäre Sicherung. Schreiben und Wiederherstellen der Empfängertabelle
wurden mit 1440p120 als bevorzugtem Timing bestätigt.

```sh
sudo python3 gc553g2.py edid-live-set --backup rx-original.bin --width 2560 --height 1440 --hz 120
sudo python3 gc553g2.py edid-live-restore rx-original.bin
```

`edid-live-set` belässt die geänderte Empfängertabelle nach dem Rücklesen
und `edid-live-restore` spielt die originale Datei auf die Karte zurück.
Die Sicherung wird vor dem Gerätezugriff geprüft. Schlägt das
Zurückspielen fehl, versucht die CLI, die vorherige Gerätetabelle
wiederherzustellen und zurückzulesen.
Beide Befehle sind an der Karte bestätigt und brauchen einen HDMI-Eingang
ohne aktives Signal. `edid-live-set` und `edid-live-test` akzeptieren auch
`--vic 118` anstelle von Breite, Höhe und Bildrate. Das priorisiert den
bereits in der Tabelle vorhandenen 4K120-CTA-Modus. Die CTA-Variante
wurde mit dem EDID-Abbild der Karte geprüft. Die tatsächlich gewählte Auflösung der HDMI-Quelle
und das dauerhafte Schreiben der gespeicherten HDMI-Presets sind offen.
Dauerhafte Presets laufen in der Originalsoftware über Realteks
Programmiermodus. Firmware-Updates sind außerhalb des Projektumfangs.

`sudo python3 gc553g2.py hdmi-status` zeigt bei stabilem Eingangssignal
unter `hdmi_input.hdr_detected` das HDR-Statusbit an. Der USB-Mitschnitt
zeigt den Wechsel des Bits bei der Umschaltung zwischen HDR und SDR an der PS5.

`sudo python3 gc553g2.py hdr-to-sdr` liest den Geräteschalter aus.
Die optionale Angabe `on` oder `off` setzt ihn auf Firmware 1.0.10.2
und liest den Wert zurück. Das Umschalten von Aus auf Ein und zurück
wurde auf der Testkarte per Readback bestätigt. Bei einem festen
PS5-Bild mit PQ-Eingang hatte der Schalter in NV12 und P010 keinen
messbaren Einfluss auf die erfassten Luma-Werte. V4L2 meldete in beiden
Schalterstellungen Rec.709. `sudo python3 gc553g2.py hdr-metadata`
fragt die HDR-Metadaten des HDMI-Empfängers ab und zeigt bei einem
erkannten InfoFrame die Eingangstransferfunktion. Die PS5 meldete
im HDR-Anpassungsmenü PQ ohne gesetzte Mastering-Werte.

## Automatisches VRR

Schließe Aufnahmeprogramme vor dem Umschalten mit
`sudo python3 gc553g2.py auto-vrr on` oder
`sudo python3 gc553g2.py auto-vrr off`. Der Befehl prüft auf Firmware
1.0.10.2 die im Windows-Mitschnitt beobachtete Geräteantwort. Die tatsächliche
variable Bildrate am HDMI-Ausgang und ein separates Auslesen der Einstellung
müssen noch geprüft werden.

## Audio, HDCP und RGB

```sh
sudo python3 gc553g2.py audio
sudo python3 gc553g2.py audio hdmi
sudo python3 gc553g2.py audio hdmi -6
sudo python3 gc553g2.py audio-bitstream
sudo python3 gc553g2.py hdcp-detection
sudo python3 gc553g2.py rgb status
sudo python3 gc553g2.py rgb 00a0ff --brightness 30
sudo python3 gc553g2.py rgb autonomous
sudo python3 gc553g2.py rgb-effect rainbow-wave
```

`rgb-effect --help` listet die 15 nativen Effekte. Wenn zuvor eine statische
Farbe über LampArray gesetzt wurde, gibt `rgb autonomous` die Beleuchtung
wieder an die Karte zurück. Die Effektbefehle verwenden die Parameter aus dem
Windows-Mitschnitt. Die sichtbare Ausgabe unter Linux sowie Farbe, Tempo und
Helligkeit der Effekte müssen noch geprüft werden.

`audio` liest die Verstärkung für HDMI, Kopfhörer (`headphones`),
Mikrofon (`microphone`) und Controller. Ein Kanalname mit Zahlenwert
setzt die Verstärkung innerhalb des jeweiligen Gerätebereichs und
liest sie zurück. `audio-bitstream` liest den Schalter für Nicht-PCM-Audio
über USB. `hdcp-detection` liest die HDCP-Erkennung der Karte. Eine
optionale Angabe `on` oder `off` setzt den jeweiligen Geräteschalter.
Die Schreibbefehle verwenden Firmware 1.0.10.2. Auf der Testkarte
wurden alle vier Pegel gelesen. HDMI-Pegel, Nicht-PCM-Ausgabe und
HDCP-Erkennung ließen sich ändern und auf ihre ursprünglichen Werte
zurückstellen. Die Audioeffekte am Signal, Party-Chat und die Dekodierung
von Dolby Digital 5.1 stehen für die Hardwareprüfung aus.
Laut AVerMedia unterstützt die Karte LPCM mit zwei Kanälen.
[AVerMedia zu 5.1-Audio](https://www.avermedia.com/pk/support/faq/can-gc553g2-support-5-1-surround-sound)

`rgb` nutzt die LampArray-Schnittstelle über `hidraw`. Für eine statische
Farbe regelt `--brightness` die Helligkeit von 0 bis 100 Prozent. Ohne
Angabe gelten 100 Prozent. `autonomous` übergibt die Steuerung wieder an
die Karte. Der Befehl findet die RGB-Schnittstelle anhand der
Seriennummer und benötigt Zugriff auf den entsprechenden Geräteknoten.
Die Karte meldete zehn LEDs. Blau und die Rückkehr zum Geräteeffekt
wurden sichtbar bestätigt. Die Helligkeitsstufen wurden mit simulierten
HID-Reports geprüft.

Bestätigt unter CachyOS sind PS5-Passthrough mit 3840×2160 bei 119,88 Hz und
USB-Aufnahme mit 3840×2160 MJPEG bei 120 fps. Mit Nioh Remastered
lieferten 1920×1080 und 2560×1440 in MJPEG rund 120 fps mit jeweils 357
aufeinanderfolgenden unterschiedlichen Bildern. 1920×1080 bei 120 fps
ließ sich auch in P010 erfassen. Der Treiber meldete dabei Rec.709.
Die Linux-CLI enthält Quellcode
und braucht keine spezielle OBS-Version. Die OBS-Aufnahmeleistung hängt zusätzlich
von Decoder, GPU und Treiber ab.
