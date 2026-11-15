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
- **Eigener Dienst `dlna_musikauswahl.load_media`** für Automationen und Skripte.
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

`media_player.play_media` funktioniert ebenfalls, inklusive `enqueue: add|next|play|replace`.
Neben `dlnamusik://`-IDs nimmt der Dienst auch `media-source://`-IDs (z. B. vom
Home-Assistant-Medienordner oder der Core-Integration *DLNA Digital Media Server*) und
direkte HTTP-URLs an. Diese werden als einzelner Titel eingefügt.

## Hinweise

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
