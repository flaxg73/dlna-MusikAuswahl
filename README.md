<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/flaxg73/dlna-MusikAuswahl/main/custom_components/dlna_musikauswahl/brand/dark_logo@2x.png">
    <img src="https://raw.githubusercontent.com/flaxg73/dlna-MusikAuswahl/main/custom_components/dlna_musikauswahl/brand/logo@2x.png" alt="Linn" width="300">
  </picture>
</p>

# DLNA Musikauswahl für Home Assistant (Linn-Playlist)

Eine Custom Integration für Home Assistant. Du durchsuchst damit einen oder mehrere
**DLNA/UPnP-Musikserver** (z. B. *MinimServer*, *Asset UPnP*, *Linn/Kazoo Server*,
Twonky, Synology …) direkt im Medienbrowser von Home Assistant. Ausgewählte Titel,
Alben, Ordner oder Playlists landen in der **internen Playlist deines Linn Selekt DSM**
(oder jedes anderen Linn DS/DSM bzw. OpenHome-Players) und werden dort abgespielt.

Die Musik streamt dabei direkt vom Server zum Linn. Home Assistant füllt nur die
Playlist, so wie Linn Kazoo oder BubbleUPnP es auch tun. Die Playlist bleibt deshalb
auf dem Linn erhalten und lässt sich ebenso mit Kazoo, der Linn App oder dem Gerät
selbst steuern.

## Funktionen

- **Medienbrowser**: Du navigierst durch die Ordnerstruktur aller eingerichteten Server
  mit Albumcovern. Bei mehreren Servern erscheint zuerst eine Serverliste. Große Ordner
  werden in Seiten zu 250 Einträgen aufgeteilt („Weitere …“).
- **Laden in die Linn-Playlist** mit allen Modi des Medienbrowsers:
  - *Abspielen* → Playlist ersetzen und starten
  - *Als Nächstes abspielen* → hinter dem aktuellen Titel einfügen
  - *Zur Warteschlange hinzufügen* → hinten anhängen
  - Ordner und Alben werden rekursiv geladen (Coverbilder und andere Nicht-Audio-Dateien
    werden übersprungen).
- Die **vollständigen DIDL-Metadaten** des Servers gehen an den Linn: Titel, Interpret,
  Album, Cover, Dauer, Samplerate und Bittiefe erscheinen auf Display und in der App.
- **Suche** über die Suchfunktion des Medienbrowsers (UPnP-Search, z. B. bei MinimServer
  und Asset).
- **Vollwertiger Media Player**: Play/Pause/Stop, Vor/Zurück, Spulen, Zufall/Wiederholen,
  Lautstärke/Stumm (falls der Linn die Lautstärke regelt), Quellenwahl, Standby ein/aus,
  Anzeige des aktuellen Titels mit Cover.
- Der Linn wird beim Laden automatisch aus dem Standby geweckt und auf die Quelle
  *Playlist* umgeschaltet.
- **Playlist-Karte fürs Dashboard** (`custom:linn-playlist-card`): Sie zeigt die Linn-Playlist
  mit Cover, Interpret, Album und Dauer, der laufende Titel ist markiert. Per Klick spielst du
  einen Titel ab, „Als Nächstes“ schiebt ihn hinter den aktuellen. Im Bearbeiten-Modus
  verschiebst du Titel (ziehen oder ▲/▼), entfernst einzelne oder mehrere und leerst die
  Playlist.
- **Bibliothek-Karte fürs Dashboard** (`custom:linn-library-card`): Musikauswahl im gleichen
  Stil. Du blätterst durch die Server mit Zurück-Pfad, Alben erscheinen als Cover-Raster, Titel
  als Liste. Die Albumansicht hat einen Kopf mit „Abspielen / Als Nächstes / Hinzufügen“.
  Dazu kommen Suche und ein Menü pro Eintrag. Die Karte zeigt, wie viele Titel geladen wurden,
  und merkt sich, wo du zuletzt warst.
- **Ordner „Aktuelle Linn-Playlist“** im Medienbrowser.
- **Dienste** `load_media`, `play_track`, `move_track`, `remove_tracks` für Automationen
  und Skripte.
- **Automatische Erkennung** per SSDP. Ändert sich die IP-Adresse eines Geräts, wird es
  über seine UDN wiedergefunden.

## Installation

### HACS (empfohlen)
1. HACS → Integrationen → ⋮ → *Benutzerdefinierte Repositories*.
2. Repository `https://github.com/flaxg73/dlna-MusikAuswahl`, Kategorie *Integration*.
3. „DLNA Musikauswahl“ installieren und Home Assistant neu starten.

### Manuell
Kopiere den Ordner `custom_components/dlna_musikauswahl` nach
`<config>/custom_components/` und starte Home Assistant neu.

## Einrichtung

1. *Einstellungen → Geräte & Dienste → Integration hinzufügen → „DLNA Musikauswahl“*.
   Erkennt Home Assistant den Linn bereits, erscheint er auch direkt als „Entdeckt“.
2. **Player wählen**: Den Linn aus der Liste wählen oder die URL seiner
   Gerätebeschreibung angeben (z. B. `http://192.168.1.50:55178/Ds/device.xml`. Die
   genaue Adresse liefert z. B. ein UPnP-Tool wie „UPnP Inspector“).
3. **Musikserver wählen**: Alle im Netz gefundenen DLNA-Server werden angeboten. Nicht
   gefundene Server kannst du über die URL ihrer Gerätebeschreibung ergänzen (mehrere
   durch Komma getrennt).

Später lassen sich unter *Konfigurieren* die Server ändern und die maximale Zahl an
Titeln pro Ladevorgang festlegen (Standard 500; der Linn fasst 1000 Titel).

Pro Linn-Player legst du einen Eintrag an. Hast du mehrere Linn-Geräte, richtest du die
Integration einfach mehrfach ein.

## Benutzung

### Playlist-Karte

Die Karte wird automatisch mitgeladen, du musst keine Ressource von Hand eintragen. Im
Dashboard: *Bearbeiten → Karte hinzufügen → „Linn Playlist“* (unter „Benutzerdefiniert“),
oder in YAML:

```yaml
type: custom:linn-playlist-card
entity: media_player.wohnzimmer
title: Playlist          # optional
show_header: true        # optional: aktueller Titel mit Play/Pause/Vor/Zurück
max_height: 480px        # optional: Höhe der Liste
```

- Klick auf einen Titel: abspielen. Das Symbol ⤒ (beim Überfahren) reiht den Titel als
  nächsten ein.
- ✎ öffnet den Bearbeiten-Modus. Dort Titel antippen oder ankreuzen, um sie auszuwählen
  und gemeinsam zu entfernen. Verschieben geht mit ▲/▼ oder über den Griff ≡ am rechten
  Rand. Mit ⌫ leerst du die ganze Playlist.
- Hinweis: OpenHome kennt kein „Verschieben“. Der Titel wird entfernt und neu eingefügt.
  Verschiebst du den laufenden Titel, beginnt er von vorn.

Falls die Karte nach einem Update nicht erscheint, lade die Seite einmal neu
(Browser-Cache).

### Bibliothek-Karte

Oben in der Karte wählst du mit drei Buttons die Quelle:

- **Musik-Server**: deine DLNA-Server (MinimServer, Asset, Linn/Kazoo Server …).
- **Qobuz**: Qobuz über einen DLNA-Brückenserver, z. B. **upmpdcli** mit Qobuz-Plugin oder
  **BubbleUPnP Server** (beide z. B. als Docker-Container). Den Server fügst du in der
  Integration wie jeden anderen Musikserver hinzu. Die Karte erkennt ihn automatisch (Name
  enthält „Qobuz“, „UpMpd“ oder „BubbleUPnP“), oder du wählst ihn unter *Konfigurieren →
  Qobuz-Server*. Bietet die Brücke mehrere Dienste an, öffnet die Karte direkt deren
  Qobuz-Ordner. Titel und Alben lädst du wie gewohnt in die Linn-Playlist.
- **Radio**: Das TuneIn-Verzeichnis (lokale Sender, Musik, Nachrichten, Sprachen …) mit
  Suche und, mit TuneIn-Benutzernamen, deinen TuneIn-Favoriten. Ein Sender wird auf der
  **Radio-Quelle** des Linn gespielt, deine Playlist bleibt dabei unverändert.

**TuneIn-Favoriten:** Trag unter *Konfigurieren → TuneIn-Benutzername* deinen
TuneIn-Benutzernamen ein. Ein Passwort ist nicht nötig. Im Radio-Bereich erscheint dann ganz oben
„Meine TuneIn-Favoriten“. Den Benutzernamen findest du auf tunein.com unter *Profil /
Einstellungen*. Hast du dich bei TuneIn nur über Google oder Apple angemeldet, lege dort zuerst
einen Benutzernamen fest.

Mit `sources: [server, radio]` blendest du einzelne Buttons aus.

```yaml
type: custom:linn-library-card
entity: media_player.wohnzimmer
title: Musik             # optional
track_action: play       # Klick auf einen Titel: play | next | add | replace
view: auto               # auto | grid | list
sources: [server, qobuz, radio]  # optional: angezeigte Quellen
max_height: 520px        # optional
```

**Als Button mit Vollbild-Auswahl:** Mit `display: button` zeigt die Karte im Dashboard nur eine
kompakte Kachel „Musik auswählen“ mit dem aktuellen Titel. Ein Tipp darauf öffnet die
Musikauswahl über den ganzen Bildschirm. Zurück zum Dashboard geht es nur mit ✕ oben links.
Auf breiten Bildschirmen (ab 1000 px) steht rechts daneben die Linn-Playlist; das lässt sich
mit `playlist_panel: false` abschalten.

```yaml
type: custom:linn-library-card
entity: media_player.wohnzimmer
display: button
button_name: Musik auswählen        # optional
button_icon: mdi:music-box-multiple # optional
playlist_panel: true                # optional
```

- Ein Klick auf Ordner, Interpret oder Album öffnet es. ← geht zurück, ⌂ springt zum Anfang.
  Bei nur einem Server startet die Karte direkt in dessen Hauptordner.
- Albumansicht: **Abspielen** ersetzt die Playlist. ⤒ reiht das Album als nächstes ein,
  + hängt es hinten an.
- Ein Klick auf einen Titel führt `track_action` aus. Standard ist *Jetzt abspielen*: Der
  Titel wird hinter dem laufenden eingefügt und gespielt, die Playlist bleibt erhalten.
- ⋮ (oder Rechtsklick) öffnet das Menü: Jetzt abspielen, Als Nächstes, Hinzufügen,
  Playlist ersetzen.
- 🔍 sucht im aktuellen Server bzw. Ordner nach Titel, Album oder Interpret.
- Große Ordner laden beim Scrollen automatisch nach.

Tipp: Lege Bibliothek- und Playlist-Karte nebeneinander (z. B. in einer Ansicht vom Typ
„Abschnitte“ oder in einem `horizontal-stack`).

### Medienbrowser

**Medienbrowser**: In der Seitenleiste *Medien* öffnen, unten rechts den Linn als Player
wählen, Server → Album/Ordner/Titel auswählen. Mit ▶ wird abgespielt. Das Menü (⋮ bzw.
„+“) bietet *Als Nächstes* und *Zur Warteschlange hinzufügen*.

Auch die Medien-Player-Karte im Dashboard hat einen „Medien durchsuchen“-Knopf.

**Automationen** (die Medien-ID steht im Medienbrowser bzw. in den Entwicklerwerkzeugen):

```yaml
action: dlna_musikauswahl.load_media
target:
  entity_id: media_player.wohnzimmer
data:
  media_content_id: "dlnamusik://uuid%3A1234-abcd/0%24albums%24a123"
  mode: replace     # replace | add | next | play
  play: true        # optional
```

Weitere Dienste zum Bearbeiten der Playlist (die Titel-IDs liefert die Karte bzw. das Attribut
`current_track_id`):

```yaml
action: dlna_musikauswahl.move_track      # Titel an Position 1 verschieben
target: { entity_id: media_player.wohnzimmer }
data: { track_id: 42, position: 1 }
---
action: dlna_musikauswahl.remove_tracks
target: { entity_id: media_player.wohnzimmer }
data: { track_ids: [42, 43] }
---
action: dlna_musikauswahl.play_track
target: { entity_id: media_player.wohnzimmer }
data: { track_id: 42 }
```

Die Entität hat zusätzlich die Attribute `playlist_length`, `playlist_position` und
`current_track_id`.

`media_player.play_media` funktioniert ebenfalls, inklusive `enqueue: add|next|play|replace`.
Neben `dlnamusik://`-IDs nimmt der Dienst auch `media-source://`-IDs (z. B. vom
Home-Assistant-Medienordner oder der Core-Integration *DLNA Digital Media Server*) und
direkte HTTP-URLs an. Diese werden als einzelner Titel eingefügt.

## Hinweise

- Das Linn-Logo dient nur zur Kennzeichnung der unterstützten Geräte. Linn ist eine Marke
  von Linn Products Ltd.; dieses Projekt ist kein offizielles Linn-Produkt und steht in keiner
  Verbindung zu Linn.

- Home Assistant muss Linn und Musikserver per Multicast (SSDP) erreichen, sonst klappt
  die automatische Erkennung nicht. Läuft HA in Docker, ist dafür `network_mode: host`
  nötig. Alternativ trägst du die URLs manuell ein.
- Die Integration verträgt sich mit der Core-Integration *OpenHome*. Du bekommst dann zwei
  Player-Entitäten für denselben Linn.
- Der Status wird alle 5 Sekunden abgefragt.

## Entwicklung / Tests

Die Tests starten einen simulierten Linn-Player (OpenHome Playlist/Product/Volume/Info/Time)
und einen simulierten DLNA-Server und prüfen die Integration in einer echten
Home-Assistant-Testinstanz:

```bash
pip install -r requirements_test.txt
pytest
```
