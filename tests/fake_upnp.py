"""Fake UPnP devices for the tests: an OpenHome renderer and a DLNA media server."""

from __future__ import annotations

import base64
import re
import struct
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from aiohttp import web

RENDERER_UDN = "uuid:4c494e4e-0000-0000-0000-000000000001"
SERVER_UDN = "uuid:6d696e69-0000-0000-0000-000000000002"
QOBUZ_UDN = "uuid:71626f7a-0000-0000-0000-000000000003"

OH = "urn:av-openhome-org:service:"
CD = "urn:schemas-upnp-org:service:ContentDirectory:1"
CM = "urn:schemas-upnp-org:service:ConnectionManager:1"

# action name -> (in arguments, out arguments); argument = (name, type)
SERVICES: dict[str, dict[str, tuple[list[tuple[str, str]], list[tuple[str, str]]]]] = {
    f"{OH}Playlist:1": {
        "Play": ([], []),
        "Pause": ([], []),
        "Stop": ([], []),
        "Next": ([], []),
        "Previous": ([], []),
        "SeekId": ([("Value", "ui4")], []),
        "SeekSecondAbsolute": ([("Value", "ui4")], []),
        "TransportState": ([], [("Value", "string")]),
        "Id": ([], [("Value", "ui4")]),
        "ReadList": ([("IdList", "string")], [("TrackList", "string")]),
        "Insert": (
            [("AfterId", "ui4"), ("Uri", "string"), ("Metadata", "string")],
            [("NewId", "ui4")],
        ),
        "DeleteId": ([("Value", "ui4")], []),
        "DeleteAll": ([], []),
        "TracksMax": ([], [("Value", "ui4")]),
        "IdArray": ([], [("Token", "ui4"), ("Array", "bin.base64")]),
        "Shuffle": ([], [("Value", "boolean")]),
        "SetShuffle": ([("Value", "boolean")], []),
        "Repeat": ([], [("Value", "boolean")]),
        "SetRepeat": ([("Value", "boolean")], []),
    },
    f"{OH}Product:1": {
        "Standby": ([], [("Value", "boolean")]),
        "SetStandby": ([("Value", "boolean")], []),
        "SourceXml": ([], [("Value", "string")]),
        "SourceIndex": ([], [("Value", "ui4")]),
        "SetSourceIndex": ([("Value", "ui4")], []),
    },
    f"{OH}Volume:1": {
        "Volume": ([], [("Value", "ui4")]),
        "SetVolume": ([("Value", "ui4")], []),
        "VolumeMax": ([], [("Value", "ui4")]),
        "Mute": ([], [("Value", "boolean")]),
        "SetMute": ([("Value", "boolean")], []),
    },
    f"{OH}Radio:1": {
        "Play": ([], []),
        "Pause": ([], []),
        "Stop": ([], []),
        "TransportState": ([], [("Value", "string")]),
        "Id": ([], [("Value", "ui4")]),
        "SetId": ([("Value", "ui4"), ("Uri", "string")], []),
        "SetChannel": ([("Uri", "string"), ("Metadata", "string")], []),
        "Channel": ([], [("Uri", "string"), ("Metadata", "string")]),
        "IdArray": ([], [("Token", "ui4"), ("Array", "bin.base64")]),
        "ReadList": ([("IdList", "string")], [("ChannelList", "string")]),
        "ChannelsMax": ([], [("Value", "ui4")]),
    },
    f"{OH}Info:1": {
        "Track": ([], [("Uri", "string"), ("Metadata", "string")]),
        "Details": ([], [("Duration", "ui4"), ("BitRate", "ui4"), ("BitDepth", "ui4"),
                         ("SampleRate", "ui4"), ("Lossless", "boolean"), ("CodecName", "string")]),
    },
    f"{OH}Time:1": {
        "Time": ([], [("TrackCount", "ui4"), ("Duration", "ui4"), ("Seconds", "ui4")]),
    },
    CD: {
        "Browse": (
            [
                ("ObjectID", "string"),
                ("BrowseFlag", "string"),
                ("Filter", "string"),
                ("StartingIndex", "ui4"),
                ("RequestedCount", "ui4"),
                ("SortCriteria", "string"),
            ],
            [
                ("Result", "string"),
                ("NumberReturned", "ui4"),
                ("TotalMatches", "ui4"),
                ("UpdateID", "ui4"),
            ],
        ),
        "Search": (
            [
                ("ContainerID", "string"),
                ("SearchCriteria", "string"),
                ("Filter", "string"),
                ("StartingIndex", "ui4"),
                ("RequestedCount", "ui4"),
                ("SortCriteria", "string"),
            ],
            [
                ("Result", "string"),
                ("NumberReturned", "ui4"),
                ("TotalMatches", "ui4"),
                ("UpdateID", "ui4"),
            ],
        ),
        "GetSearchCapabilities": ([], [("SearchCaps", "string")]),
        "GetSortCapabilities": ([], [("SortCaps", "string")]),
        "GetSystemUpdateID": ([], [("Id", "ui4")]),
    },
    CM: {
        "GetProtocolInfo": ([], [("Source", "string"), ("Sink", "string")]),
    },
}


def _short(service_type: str) -> str:
    return service_type.split(":")[-2]


def scpd(service_type: str) -> str:
    """Build the SCPD document of a service."""
    actions = []
    variables: dict[str, str] = {}
    for name, (ins, outs) in SERVICES[service_type].items():
        args = []
        for direction, arg_list in (("in", ins), ("out", outs)):
            for arg, typ in arg_list:
                var = f"A_ARG_{name}_{arg}"
                variables[var] = typ
                args.append(
                    f"<argument><name>{arg}</name><direction>{direction}</direction>"
                    f"<relatedStateVariable>{var}</relatedStateVariable></argument>"
                )
        actions.append(
            f"<action><name>{name}</name><argumentList>{''.join(args)}</argumentList></action>"
        )
    state = "".join(
        f'<stateVariable sendEvents="no"><name>{n}</name><dataType>{t}</dataType></stateVariable>'
        for n, t in variables.items()
    )
    return (
        '<?xml version="1.0"?><scpd xmlns="urn:schemas-upnp-org:service-1-0">'
        "<specVersion><major>1</major><minor>0</minor></specVersion>"
        f"<actionList>{''.join(actions)}</actionList>"
        f"<serviceStateTable>{state}</serviceStateTable></scpd>"
    )


def device_xml(
    device_type: str, name: str, udn: str, services: list[str], prefix: str = ""
) -> str:
    """Build a device description."""
    service_xml = "".join(
        f"<service><serviceType>{st}</serviceType>"
        f"<serviceId>urn:{'upnp-org' if 'schemas-upnp' in st else 'av-openhome-org'}"
        f":serviceId:{_short(st)}</serviceId>"
        f"<SCPDURL>/{_short(st)}.xml</SCPDURL>"
        f"<controlURL>{prefix}/ctl/{_short(st)}</controlURL>"
        f"<eventSubURL>{prefix}/evt/{_short(st)}</eventSubURL></service>"
        for st in services
    )
    return (
        '<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">'
        "<specVersion><major>1</major><minor>0</minor></specVersion><device>"
        f"<deviceType>{device_type}</deviceType><friendlyName>{name}</friendlyName>"
        "<manufacturer>Linn</manufacturer><modelName>Selekt DSM</modelName>"
        f"<UDN>{udn}</UDN><serviceList>{service_xml}</serviceList></device></root>"
    )


# --------------------------------------------------------------------- library
DIDL_OPEN = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
    'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/" '
    'xmlns:pv="http://www.pv.com/pvns/">'
)


class Library:
    """A tiny music library: root -> Albums -> 2 albums with tracks, plus a big folder."""

    def __init__(self, base_url: str) -> None:
        """Create the library."""
        self.base = base_url
        self.objects: dict[str, dict[str, Any]] = {}
        self.children: dict[str, list[str]] = {}
        self._add("0", "-1", "Root", "object.container")
        self._add("albums", "0", "Alben", "object.container")
        self._add("big", "0", "Alle Titel", "object.container")
        for album_no in (1, 2):
            album = f"album{album_no}"
            self._add(album, "albums", f"Album {album_no}", "object.container.album.musicAlbum",
                      art=f"/art/{album}.jpg")
            for track_no in range(1, 4):
                self._add(
                    f"{album}-t{track_no}", album, f"Track {album_no}.{track_no}",
                    "object.item.audioItem.musicTrack", art=f"/art/{album}.jpg",
                    album=f"Album {album_no}", track=track_no,
                )
            # a cover image item that must be ignored
            self._add(f"{album}-cover", album, "cover.jpg", "object.item.imageItem.photo")
        for i in range(600):
            self._add(f"big-{i}", "big", f"Song {i}", "object.item.audioItem.musicTrack",
                      album="Big", track=i)

    def _add(self, oid, parent, title, upnp_class, art=None, album=None, track=None):
        self.objects[oid] = {
            "id": oid, "parent": parent, "title": title, "class": upnp_class,
            "art": art, "album": album, "track": track,
        }
        self.children.setdefault(oid, [])
        if parent in self.children:
            self.children[parent].append(oid)

    def didl(self, oid: str) -> str:
        obj = dict(self.objects[oid])
        if obj["art"] and obj["art"].startswith("http"):
            obj["art"] = obj["art"].removeprefix(self.base)  # absolute URL, no prefix
            base_saved, self.base = self.base, ""
            try:
                return self._didl(oid, obj)
            finally:
                self.base = base_saved
        return self._didl(oid, obj)

    def _didl(self, oid: str, obj: dict) -> str:
        title = escape(obj["title"])
        if obj["class"].startswith("object.container"):
            art = (f"<upnp:albumArtURI>{self.base}{obj['art']}</upnp:albumArtURI>"
                   if obj["art"] else "")
            return (
                f'<container id="{oid}" parentID="{obj["parent"]}" restricted="1" '
                f'childCount="{len(self.children[oid])}"><dc:title>{title}</dc:title>'
                f"<upnp:class>{obj['class']}</upnp:class>{art}</container>"
            )
        if obj["class"].startswith("object.item.imageItem"):
            return (
                f'<item id="{oid}" parentID="{obj["parent"]}" restricted="1">'
                f"<dc:title>{title}</dc:title><upnp:class>{obj['class']}</upnp:class>"
                f'<res protocolInfo="http-get:*:image/jpeg:*">{self.base}/img/{oid}.jpg</res>'
                "</item>"
            )
        art = (f'<upnp:albumArtURI dlna:profileID="JPEG_TN">{self.base}{obj["art"]}'
               "</upnp:albumArtURI>" if obj["art"] else "")
        return (
            f'<item id="{oid}" parentID="{obj["parent"]}" restricted="1">'
            f"<dc:title>{title}</dc:title><upnp:class>{obj['class']}</upnp:class>"
            f"<upnp:artist>The Band</upnp:artist><upnp:album>{escape(obj['album'] or '')}</upnp:album>"
            f"<upnp:originalTrackNumber>{obj['track']}</upnp:originalTrackNumber>{art}"
            "<pv:extension>vendor</pv:extension>"
            f'<res protocolInfo="http-get:*:audio/x-flac:*" duration="0:04:05.000" '
            f'sampleFrequency="96000" bitsPerSample="24">{self.base}/music/{oid}.flac</res>'
            "</item>"
        )


def qobuz_library(base_url: str) -> Library:
    """Library of a DLNA bridge like BubbleUPnP Server: Qobuz next to other services."""
    lib = Library.__new__(Library)
    lib.base = base_url
    lib.objects = {}
    lib.children = {}
    lib._add("0", "-1", "Root", "object.container")
    lib._add("q", "0", "Qobuz", "object.container")
    lib._add("tidal", "0", "TIDAL", "object.container")
    lib._add("q-albums", "q", "Meine Alben", "object.container")
    lib._add("qa1", "q-albums", "Qobuz Album", "object.container.album.musicAlbum",
             art="https://static.qobuz.example/qa1.jpg")
    for n in (1, 2):
        lib._add(f"qa1-t{n}", "qa1", f"Qobuz Track {n}", "object.item.audioItem.musicTrack",
                 album="Qobuz Album", track=n)
    return lib


TUNEIN_ROOT = {
    "head": {"title": "Browse", "status": "200"},
    "body": [
        {"element": "outline", "type": "link", "text": "Lokales Radio",
         "URL": "http://opml.radiotime.com/Browse.ashx?c=local", "key": "local"},
        {"element": "outline", "type": "link", "text": "Musik",
         "URL": "http://opml.radiotime.com/Browse.ashx?c=music", "key": "music"},
    ],
}
TUNEIN_LOCAL = {
    "head": {"title": "Lokales Radio", "status": "200"},
    "body": [
        {"element": "outline", "text": "Sender", "key": "stations", "children": [
            {"element": "outline", "type": "audio", "text": "Radio Eins",
             "URL": "http://opml.radiotime.com/Tune.ashx?id=s1001", "guide_id": "s1001",
             "subtext": "Nur für Erwachsene", "bitrate": "128", "formats": "mp3",
             "image": "http://cdn-radiotime-logos.tunein.com/s1001q.png", "item": "station"},
            {"element": "outline", "type": "audio", "text": "Jazz FM",
             "URL": "http://opml.radiotime.com/Tune.ashx?id=s1002", "guide_id": "s1002",
             "bitrate": "64", "formats": "aac", "item": "station"},
        ]},
    ],
}


# --------------------------------------------------------------------- server
class SoapFault(Exception):
    """UPnP error returned by a fake action."""

    def __init__(self, code: int, text: str) -> None:
        """Store code and description."""
        super().__init__(text)
        self.code = code
        self.text = text


class FakeUpnp:
    """HTTP server hosting both fake devices and recording their state."""

    def __init__(self) -> None:
        """Create the state."""
        self.base = ""
        self.library: Library | None = None
        self.qobuz: Library | None = None
        # radio state
        self.radio_channel: tuple[str, str] | None = None
        self.radio_id = 0
        self.radio_transport = "Stopped"
        self.radio_presets = {
            7: ("http://streams.example/bayern3.mp3", "Bayern 3"),
            9: ("http://streams.example/fm4.mp3", "FM4"),
        }
        self.tunein_requests: list[str] = []
        self.details_broken = False
        self.random_searches = 0
        # renderer state
        self.playlist: list[tuple[int, str, str]] = []
        self.next_id = 1
        self.current_id = 0
        self.transport = "Stopped"
        self.standby = True
        self.source_index = 2
        self.volume = 30
        self.mute = False
        self.shuffle = False
        self.repeat = False
        self.calls: list[str] = []
        # simulate servers with broken BrowseMetadata: None | "no_res" | "error"
        self.metadata_quirk: str | None = None
        # simulate picky Search implementations: "root_only", "strict", "no_res", "unsupported"
        self.search_quirks: set[str] = set()

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/renderer.xml", self._renderer_xml)
        app.router.add_get("/server.xml", self._server_xml)
        app.router.add_get("/qobuz.xml", self._qobuz_xml)
        app.router.add_post("/q/ctl/{svc}", self._control)
        app.router.add_get("/opml/{endpoint}", self._opml)
        app.router.add_get("/streams/{name}", self._stream_playlist)
        app.router.add_get("/art/{name}", self._art)
        app.router.add_get(r"/{svc}.xml", self._scpd)
        app.router.add_post("/ctl/{svc}", self._control)
        return app

    async def _renderer_xml(self, request: web.Request) -> web.Response:
        return web.Response(
            text=device_xml(
                "urn:linn-co-uk:device:Source:1", "Wohnzimmer", RENDERER_UDN,
                [f"{OH}{s}:1" for s in ("Product", "Playlist", "Volume", "Info", "Time", "Radio")],
            ),
            content_type="text/xml",
        )

    async def _server_xml(self, request: web.Request) -> web.Response:
        return web.Response(
            text=device_xml(
                "urn:schemas-upnp-org:device:MediaServer:1", "MinimServer", SERVER_UDN, [CD, CM]
            ),
            content_type="text/xml",
        )

    async def _qobuz_xml(self, request: web.Request) -> web.Response:
        return web.Response(
            text=device_xml(
                "urn:schemas-upnp-org:device:MediaServer:1", "UpMpd-mediaserver", QOBUZ_UDN,
                [CD, CM], prefix="/q",
            ),
            content_type="text/xml",
        )

    async def _opml(self, request: web.Request) -> web.Response:
        endpoint = request.match_info["endpoint"]
        query = dict(request.query)
        self.tunein_requests.append(f"{endpoint}?{request.query_string}")
        assert query.get("render") == "json"
        if endpoint == "Browse.ashx" and query.get("c") == "presets" and "@" in query.get("username", ""):
            raise web.HTTPForbidden
        if (endpoint == "Browse.ashx" and query.get("c") == "presets"
                and query.get("username") == "picky" and query.get("partnerId") != "1"):
            raise web.HTTPForbidden  # only answers with a partner id
        if endpoint == "Browse.ashx" and query.get("c") == "presets":
            body = (TUNEIN_LOCAL["body"][0]["children"][:1]
                    if query.get("username") in ("flo", "picky") else [])
            data = {"head": {"title": "Presets", "status": "200"}, "body": body}
        elif endpoint == "Browse.ashx":
            data = TUNEIN_LOCAL if query.get("c") == "local" else TUNEIN_ROOT
        elif endpoint == "Search.ashx":
            hits = [s for s in TUNEIN_LOCAL["body"][0]["children"]
                    if query["query"].lower() in s["text"].lower()]
            data = {"head": {"status": "200"}, "body": hits}
        elif endpoint == "Tune.ashx":
            if query["id"] == "s1002":  # playlist file, not a direct stream
                body = [{"url": f"{self.base}/streams/jazz.pls", "is_direct": False}]
            else:
                body = [{"url": f"http://streams.example/{query['id']}.mp3", "is_direct": True,
                         "bitrate": 128, "media_type": "mp3"}]
            data = {"head": {"status": "200"}, "body": body}
        elif endpoint == "Describe.ashx":
            data = {"head": {"status": "200"},
                    "body": [{"element": "station", "name": "Described FM", "logo": None}]}
        else:
            raise web.HTTPNotFound
        return web.json_response(data)

    async def _stream_playlist(self, request: web.Request) -> web.Response:
        return web.Response(text="[playlist]\nNumberOfEntries=1\nFile1=http://streams.example/jazz.aac\n")

    async def _art(self, request: web.Request) -> web.Response:
        return web.Response(body=b"\xff\xd8JPEGDATA", content_type="image/jpeg")

    async def _scpd(self, request: web.Request) -> web.Response:
        short = request.match_info["svc"]
        for st in SERVICES:
            if _short(st) == short:
                return web.Response(text=scpd(st), content_type="text/xml")
        raise web.HTTPNotFound

    async def _control(self, request: web.Request) -> web.Response:
        short = request.match_info["svc"]
        service_type = next(st for st in SERVICES if _short(st) == short)
        self._active_library = self.qobuz if request.path.startswith("/q/") else self.library
        body = ET.fromstring(await request.text())
        action_el = next(iter(next(iter(body))))
        action = action_el.tag.rsplit("}", 1)[-1]
        args = {child.tag.rsplit("}", 1)[-1]: child.text or "" for child in action_el}
        self.calls.append(f"{short}.{action}")
        try:
            result = getattr(self, f"_{short}_{action}")(**args)
        except SoapFault as fault:
            return web.Response(
                status=500,
                text=(
                    '<?xml version="1.0"?><s:Envelope '
                    'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault>'
                    "<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring>"
                    '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
                    f"<errorCode>{fault.code}</errorCode>"
                    f"<errorDescription>{fault.text}</errorDescription>"
                    "</UPnPError></detail></s:Fault></s:Body></s:Envelope>"
                ),
                content_type="text/xml",
            )
        out = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in result.items())
        return web.Response(
            text=(
                '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action}Response xmlns:u="{service_type}">{out}</u:{action}Response>'
                "</s:Body></s:Envelope>"
            ),
            content_type="text/xml",
            charset="utf-8",
        )

    # ---------------------------------------------------------------- playlist
    def _ids(self) -> list[int]:
        return [entry[0] for entry in self.playlist]

    def _Playlist_Play(self):
        if self.playlist:
            self.transport = "Playing"
            if self.current_id not in self._ids():
                self.current_id = self.playlist[0][0]
        return {}

    def _Playlist_Pause(self):
        self.transport = "Paused"
        return {}

    def _Playlist_Stop(self):
        self.transport = "Stopped"
        return {}

    def _Playlist_Next(self):
        ids = self._ids()
        if self.current_id in ids and ids.index(self.current_id) + 1 < len(ids):
            self.current_id = ids[ids.index(self.current_id) + 1]
        return {}

    def _Playlist_Previous(self):
        ids = self._ids()
        if self.current_id in ids and ids.index(self.current_id) > 0:
            self.current_id = ids[ids.index(self.current_id) - 1]
        return {}

    def _Playlist_SeekId(self, Value):
        assert int(Value) in self._ids()
        self.current_id = int(Value)
        return {}

    def _Playlist_SeekSecondAbsolute(self, Value):
        return {}

    def _Playlist_TransportState(self):
        return {"Value": self.transport}

    def _Playlist_Id(self):
        return {"Value": self.current_id}

    def _Playlist_ReadList(self, IdList):
        wanted = [int(i) for i in IdList.split()]
        entries = "".join(
            f"<Entry><Id>{i}</Id><Uri>{escape(u)}</Uri><Metadata>{escape(m)}</Metadata></Entry>"
            for i, u, m in self.playlist
            if i in wanted
        )
        return {"TrackList": f"<TrackList>{entries}</TrackList>"}

    def _Playlist_Insert(self, AfterId, Uri, Metadata):
        after = int(AfterId)
        ET.fromstring(Metadata)  # metadata must be well formed XML
        new_id = self.next_id
        self.next_id += 1
        index = 0 if after == 0 else self._ids().index(after) + 1
        self.playlist.insert(index, (new_id, Uri, Metadata))
        return {"NewId": new_id}

    def _Playlist_DeleteId(self, Value):
        self.playlist = [e for e in self.playlist if e[0] != int(Value)]
        return {}

    def _Playlist_DeleteAll(self):
        self.playlist = []
        self.current_id = 0
        self.transport = "Stopped"
        return {}

    def _Playlist_TracksMax(self):
        return {"Value": 1000}

    def _Playlist_IdArray(self):
        raw = struct.pack(f">{len(self.playlist)}I", *self._ids())
        return {"Token": 1, "Array": base64.b64encode(raw).decode()}

    def _Playlist_Shuffle(self):
        return {"Value": int(self.shuffle)}

    def _Playlist_SetShuffle(self, Value):
        self.shuffle = Value in ("1", "true")
        return {}

    def _Playlist_Repeat(self):
        return {"Value": int(self.repeat)}

    def _Playlist_SetRepeat(self, Value):
        self.repeat = Value in ("1", "true")
        return {}

    # ----------------------------------------------------------------- product
    def _Product_Standby(self):
        return {"Value": int(self.standby)}

    def _Product_SetStandby(self, Value):
        self.standby = Value in ("1", "true")
        return {}

    def _Product_SourceXml(self):
        sources = [("Playlist", "Playlist"), ("Radio", "Radio"), ("TV", "Hdmi")]
        xml = "".join(
            f"<Source><Name>{n}</Name><Type>{t}</Type><Visible>true</Visible></Source>"
            for n, t in sources
        )
        return {"Value": f"<SourceList>{xml}</SourceList>"}

    def _Product_SourceIndex(self):
        return {"Value": self.source_index}

    def _Product_SetSourceIndex(self, Value):
        self.source_index = int(Value)
        return {}

    # ------------------------------------------------------------------ volume
    def _Volume_Volume(self):
        return {"Value": self.volume}

    def _Volume_SetVolume(self, Value):
        self.volume = int(Value)
        return {}

    def _Volume_VolumeMax(self):
        return {"Value": 100}

    def _Volume_Mute(self):
        return {"Value": int(self.mute)}

    def _Volume_SetMute(self, Value):
        self.mute = Value in ("1", "true")
        return {}

    # ------------------------------------------------------------------- radio
    def _Radio_Play(self):
        self.radio_transport = "Playing"
        return {}

    def _Radio_Pause(self):
        self.radio_transport = "Paused"
        return {}

    def _Radio_Stop(self):
        self.radio_transport = "Stopped"
        return {}

    def _Radio_TransportState(self):
        return {"Value": self.radio_transport}

    def _Radio_Id(self):
        return {"Value": self.radio_id}

    def _Radio_SetId(self, Value, Uri):
        assert self.radio_presets[int(Value)][0] == Uri
        self.radio_id = int(Value)
        self.radio_channel = (Uri, "")
        return {}

    def _Radio_SetChannel(self, Uri, Metadata):
        ET.fromstring(Metadata)
        self.radio_id = 0
        self.radio_channel = (Uri, Metadata)
        return {}

    def _Radio_Channel(self):
        uri, metadata = self.radio_channel or ("", "")
        return {"Uri": uri, "Metadata": metadata}

    def _Radio_IdArray(self):
        ids = [7, 0, 9]  # 0 = empty preset slot
        raw = struct.pack(f">{len(ids)}I", *ids)
        return {"Token": 1, "Array": base64.b64encode(raw).decode()}

    def _Radio_ReadList(self, IdList):
        entries = ""
        for i in (int(x) for x in IdList.split()):
            uri, name = self.radio_presets[i]
            meta = (f'{DIDL_OPEN}<item id="{i}" parentID="-1" restricted="1"><dc:title>{name}</dc:title>'
                    "<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>"
                    f"<upnp:albumArtURI>https://logos.example/{i}.png</upnp:albumArtURI>"
                    f'<res protocolInfo="http-get:*:audio/mpeg:*">{uri}</res></item></DIDL-Lite>')
            entries += f"<Entry><Id>{i}</Id><Metadata>{escape(meta)}</Metadata></Entry>"
        return {"ChannelList": f"<ChannelList>{entries}</ChannelList>"}

    def _Radio_ChannelsMax(self):
        return {"Value": 100}

    # -------------------------------------------------------------- info/time
    def _Info_Track(self):
        for i, uri, metadata in self.playlist:
            if i == self.current_id and self.source_index == 0:
                return {"Uri": uri, "Metadata": metadata}
        return {"Uri": "", "Metadata": ""}

    def _Info_Details(self):
        if self.details_broken:
            raise SoapFault(501, "Action failed")
        if not self.current_id:
            return {"Duration": 0, "BitRate": 0, "BitDepth": 0, "SampleRate": 0,
                    "Lossless": 0, "CodecName": ""}
        return {"Duration": 245, "BitRate": 4608000, "BitDepth": 24, "SampleRate": 96000,
                "Lossless": 1, "CodecName": "FLAC"}

    def _Time_Time(self):
        return {"TrackCount": 0, "Duration": 245 if self.current_id else 0, "Seconds": 12}

    # -------------------------------------------------------- content directory
    def _ContentDirectory_Browse(self, ObjectID, BrowseFlag, Filter, StartingIndex,
                                 RequestedCount, SortCriteria):
        lib = self._active_library
        assert lib is not None
        if BrowseFlag == "BrowseMetadata":
            if self.metadata_quirk == "error":
                raise SoapFault(701, "No such object")
            if self.metadata_quirk == "id_title":  # like upmpdcli plugins: id as title
                result = (
                    f'{DIDL_OPEN}<container id="{escape(ObjectID)}" restricted="1">'
                    f"<dc:title>{escape(ObjectID)}</dc:title>"
                    "<upnp:class>object.container</upnp:class></container></DIDL-Lite>"
                )
                return {"Result": result, "NumberReturned": 1, "TotalMatches": 1,
                        "UpdateID": 1}
            if self.metadata_quirk == "no_res":
                obj = lib.objects[ObjectID]
                result = (
                    f'{DIDL_OPEN}<item id="{ObjectID}" restricted="1">'
                    f"<dc:title>{escape(obj['title'])}</dc:title>"
                    "<upnp:class>object.item</upnp:class></item></DIDL-Lite>"
                )
                return {"Result": result, "NumberReturned": 1, "TotalMatches": 1,
                        "UpdateID": 1}
            ids = [ObjectID]
            total = 1
        else:
            children = lib.children[ObjectID]
            start = int(StartingIndex)
            count = int(RequestedCount) or len(children)
            ids = children[start : start + count]
            total = len(children)
        result = DIDL_OPEN + "".join(lib.didl(i) for i in ids) + "</DIDL-Lite>"
        return {"Result": result, "NumberReturned": len(ids), "TotalMatches": total,
                "UpdateID": 1}

    def _ContentDirectory_Search(self, ContainerID, SearchCriteria, Filter, StartingIndex,
                                 RequestedCount, SortCriteria):
        lib = self._active_library
        assert lib is not None
        quirks = self.search_quirks
        if "unsupported" in quirks:
            raise SoapFault(401, "Invalid Action")
        if "root_only" in quirks and ContainerID != "0":
            raise SoapFault(710, "No such container")
        if "strict" in quirks and (SearchCriteria.startswith("(") or " or " in SearchCriteria
                                   or "derivedfrom" in SearchCriteria):
            raise SoapFault(708, "Unsupported or invalid search criteria")
        if 'derivedfrom "object.item.audioItem"' in SearchCriteria and "contains" not in SearchCriteria:
            # all audio items below the container (used for random playlists)
            found, stack = [], [ContainerID]
            while stack:
                for child in lib.children.get(stack.pop(0), []):
                    if lib.objects[child]["class"].startswith("object.container"):
                        stack.append(child)
                    elif lib.objects[child]["class"].startswith("object.item.audioItem"):
                        found.append(child)
            self.random_searches += 1
            start, count = int(StartingIndex), int(RequestedCount) or len(found)
            ids = found[start : start + count]
            didl = "".join(lib.didl(i) for i in ids)
            return {"Result": DIDL_OPEN + didl + "</DIDL-Lite>", "NumberReturned": len(ids),
                    "TotalMatches": len(found), "UpdateID": 1}
        term = re.search(r'contains "([^"]*)"', SearchCriteria).group(1).lower()
        ids = [o for o, obj in lib.objects.items()
               if o != "0" and term in obj["title"].lower()][: int(RequestedCount) or None]
        didl = "".join(lib.didl(i) for i in ids)
        if "no_res" in quirks:
            didl = re.sub(r"<res [^>]*>[^<]*</res>", "", didl)
        result = DIDL_OPEN + didl + "</DIDL-Lite>"
        return {"Result": result, "NumberReturned": len(ids), "TotalMatches": len(ids),
                "UpdateID": 1}

    def _ContentDirectory_GetSearchCapabilities(self):
        return {"SearchCaps": "dc:title,upnp:artist,upnp:album"}

    def _ContentDirectory_GetSortCapabilities(self):
        return {"SortCaps": "dc:title"}

    def _ContentDirectory_GetSystemUpdateID(self):
        return {"Id": 1}

    def _ConnectionManager_GetProtocolInfo(self):
        return {"Source": "http-get:*:audio/x-flac:*", "Sink": ""}
