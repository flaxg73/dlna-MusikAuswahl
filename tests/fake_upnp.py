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
    f"{OH}Info:1": {
        "Track": ([], [("Uri", "string"), ("Metadata", "string")]),
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


def device_xml(device_type: str, name: str, udn: str, services: list[str]) -> str:
    """Build a device description."""
    service_xml = "".join(
        f"<service><serviceType>{st}</serviceType>"
        f"<serviceId>urn:{'upnp-org' if 'schemas-upnp' in st else 'av-openhome-org'}"
        f":serviceId:{_short(st)}</serviceId>"
        f"<SCPDURL>/{_short(st)}.xml</SCPDURL>"
        f"<controlURL>/ctl/{_short(st)}</controlURL>"
        f"<eventSubURL>/evt/{_short(st)}</eventSubURL></service>"
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
        obj = self.objects[oid]
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

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/renderer.xml", self._renderer_xml)
        app.router.add_get("/server.xml", self._server_xml)
        app.router.add_get("/art/{name}", self._art)
        app.router.add_get(r"/{svc}.xml", self._scpd)
        app.router.add_post("/ctl/{svc}", self._control)
        return app

    async def _renderer_xml(self, request: web.Request) -> web.Response:
        return web.Response(
            text=device_xml(
                "urn:linn-co-uk:device:Source:1", "Wohnzimmer", RENDERER_UDN,
                [f"{OH}{s}:1" for s in ("Product", "Playlist", "Volume", "Info", "Time")],
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

    # -------------------------------------------------------------- info/time
    def _Info_Track(self):
        for i, uri, metadata in self.playlist:
            if i == self.current_id and self.source_index == 0:
                return {"Uri": uri, "Metadata": metadata}
        return {"Uri": "", "Metadata": ""}

    def _Time_Time(self):
        return {"TrackCount": 0, "Duration": 245 if self.current_id else 0, "Seconds": 12}

    # -------------------------------------------------------- content directory
    def _ContentDirectory_Browse(self, ObjectID, BrowseFlag, Filter, StartingIndex,
                                 RequestedCount, SortCriteria):
        lib = self.library
        assert lib is not None
        if BrowseFlag == "BrowseMetadata":
            if self.metadata_quirk == "error":
                raise SoapFault(701, "No such object")
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
        lib = self.library
        assert lib is not None
        term = re.search(r'contains "([^"]*)"', SearchCriteria).group(1).lower()
        ids = [o for o, obj in lib.objects.items()
               if o != "0" and term in obj["title"].lower()][: int(RequestedCount) or None]
        result = DIDL_OPEN + "".join(lib.didl(i) for i in ids) + "</DIDL-Lite>"
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
