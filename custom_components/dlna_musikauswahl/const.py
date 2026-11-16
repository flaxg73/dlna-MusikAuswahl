"""Constants for the DLNA Musikauswahl integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "dlna_musikauswahl"

CONF_RENDERER_LOCATION: Final = "renderer_location"
CONF_RENDERER_UDN: Final = "renderer_udn"
CONF_RENDERER_NAME: Final = "renderer_name"
CONF_SERVERS: Final = "servers"
CONF_MAX_TRACKS: Final = "max_tracks"
CONF_QOBUZ_SERVER: Final = "qobuz_server"
QOBUZ_NONE: Final = "none"
QOBUZ_AUTO: Final = "auto"
QOBUZ_NAME_HINTS: Final = ("qobuz", "upmpd", "bubbleupnp")

# keys inside a stored server dict
SERVER_UDN: Final = "udn"
SERVER_NAME: Final = "name"
SERVER_LOCATION: Final = "location"

DEFAULT_MAX_TRACKS: Final = 500

# SSDP search targets
ST_OPENHOME_PLAYLIST: Final = "urn:av-openhome-org:service:Playlist:1"
ST_MEDIA_SERVERS: Final = (
    "urn:schemas-upnp-org:device:MediaServer:1",
    "urn:schemas-upnp-org:device:MediaServer:2",
    "urn:schemas-upnp-org:device:MediaServer:3",
    "urn:schemas-upnp-org:device:MediaServer:4",
)

# Media browser
URI_SCHEME: Final = "dlnamusik"
URI_SCHEME_QUEUE: Final = "dlnamusik-queue"
URI_SCHEME_RADIO: Final = "dlnamusik-radio"
BROWSE_PAGE_SIZE: Final = 250
MAX_RECURSION_DEPTH: Final = 6

# Enqueue modes of the load service
MODE_REPLACE: Final = "replace"
MODE_ADD: Final = "add"
MODE_NEXT: Final = "next"
MODE_PLAY: Final = "play"
LOAD_MODES: Final = (MODE_REPLACE, MODE_ADD, MODE_NEXT, MODE_PLAY)

SERVICE_LOAD_MEDIA: Final = "load_media"
SERVICE_PLAY_TRACK: Final = "play_track"
SERVICE_REMOVE_TRACKS: Final = "remove_tracks"
SERVICE_MOVE_TRACK: Final = "move_track"

FRONTEND_URL: Final = "/dlna_musikauswahl"
CARDS: Final = ("linn-playlist-card.js", "linn-library-card.js")
CARD_VERSION: Final = "0.5.1"
ATTR_MODE: Final = "mode"
ATTR_PLAY: Final = "play"
