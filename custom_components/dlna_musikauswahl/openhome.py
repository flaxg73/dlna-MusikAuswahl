"""Minimal OpenHome client (Linn DS/DSM) built on async_upnp_client."""

from __future__ import annotations

import base64
import logging
import struct
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from async_upnp_client.client import UpnpDevice, UpnpService
from async_upnp_client.exceptions import UpnpError

_LOGGER = logging.getLogger(__name__)

SERVICE_PREFIX = "urn:av-openhome-org:service:"


class OpenHomeError(UpnpError):
    """Raised when the renderer does not support a required action."""


@dataclass(slots=True)
class PlaylistTrack:
    """A track to insert into the renderer playlist."""

    uri: str
    metadata: str


@dataclass(slots=True)
class PlaylistEntry:
    """A track stored in the renderer playlist."""

    id: int
    uri: str
    metadata: str


@dataclass(slots=True)
class Source:
    """A source (input) of an OpenHome product."""

    index: int
    name: str
    type: str
    visible: bool


def find_openhome_service(device: UpnpDevice, name: str) -> UpnpService | None:
    """Find the newest version of an OpenHome service in the device tree."""
    best: UpnpService | None = None
    best_version = -1
    prefix = f"{SERVICE_PREFIX}{name}:"
    for service in device.all_services:
        if not service.service_type.startswith(prefix):
            continue
        try:
            version = int(service.service_type.rsplit(":", 1)[1])
        except ValueError:
            version = 0
        if version > best_version:
            best, best_version = service, version
    return best


def decode_id_array(value: Any) -> list[int]:
    """Decode the base64 encoded big endian uint32 array of IdArray."""
    if not value:
        return []
    raw = value if isinstance(value, bytes) else base64.b64decode(value)
    count = len(raw) // 4
    return list(struct.unpack(f">{count}I", raw[: count * 4]))


def parse_track_list(xml: str) -> list[PlaylistEntry]:
    """Parse the TrackList document returned by Playlist.ReadList."""
    entries: list[PlaylistEntry] = []
    if not xml:
        return entries
    root = ET.fromstring(xml)
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1] != "Entry":
            continue
        values = {child.tag.rsplit("}", 1)[-1]: child.text or "" for child in entry}
        try:
            entries.append(
                PlaylistEntry(
                    int(values.get("Id", "0")),
                    values.get("Uri", ""),
                    values.get("Metadata", ""),
                )
            )
        except ValueError:
            continue
    return entries


def parse_source_xml(xml: str) -> list[Source]:
    """Parse the SourceXml document of the Product service."""
    sources: list[Source] = []
    if not xml:
        return sources
    root = ET.fromstring(xml)
    index = 0
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "Source":
            continue
        values = {child.tag.rsplit("}", 1)[-1]: (child.text or "") for child in element}
        sources.append(
            Source(
                index=index,
                name=values.get("Name", ""),
                type=values.get("Type", ""),
                visible=values.get("Visible", "true").lower() in ("true", "1", "yes"),
            )
        )
        index += 1
    return sources


class OpenHomeRenderer:
    """Access to the Playlist, Product, Volume, Info and Time services."""

    def __init__(self, device: UpnpDevice) -> None:
        """Initialise from an already created UPnP device."""
        self.device = device
        playlist = find_openhome_service(device, "Playlist")
        if playlist is None:
            raise OpenHomeError(
                f"{device.friendly_name} does not offer the OpenHome Playlist service"
            )
        self.playlist: UpnpService = playlist
        self.product = find_openhome_service(device, "Product")
        self.volume = find_openhome_service(device, "Volume")
        self.info = find_openhome_service(device, "Info")
        self.time = find_openhome_service(device, "Time")
        self.radio = find_openhome_service(device, "Radio")
        self._sources: list[Source] | None = None

    # ------------------------------------------------------------------ helpers
    @property
    def name(self) -> str:
        """Friendly name of the renderer."""
        return self.device.friendly_name

    @property
    def udn(self) -> str:
        """UDN of the root device."""
        return self.device.udn

    @staticmethod
    async def _call(service: UpnpService | None, action: str, **kwargs: Any) -> dict:
        if service is None or not service.has_action(action):
            raise OpenHomeError(f"Action {action} is not supported by the renderer")
        result = await service.action(action).async_call(**kwargs)
        return dict(result)

    @staticmethod
    def has(service: UpnpService | None, action: str) -> bool:
        """Return True if the service exists and supports the action."""
        return service is not None and service.has_action(action)

    # ----------------------------------------------------------------- playlist
    async def ids(self) -> list[int]:
        """Return the ids of all tracks in the playlist."""
        result = await self._call(self.playlist, "IdArray")
        return decode_id_array(result.get("Array"))

    async def current_id(self) -> int:
        """Return the id of the current track (0 = none)."""
        return int((await self._call(self.playlist, "Id"))["Value"])

    async def tracks_max(self) -> int:
        """Return the maximum number of tracks the playlist can hold."""
        try:
            return int((await self._call(self.playlist, "TracksMax"))["Value"])
        except (OpenHomeError, KeyError, ValueError):
            return 1000

    async def read_list(self, ids: list[int]) -> list[PlaylistEntry]:
        """Read uri and metadata of the given playlist ids."""
        if not ids:
            return []
        result = await self._call(
            self.playlist, "ReadList", IdList=" ".join(str(i) for i in ids)
        )
        return parse_track_list(result.get("TrackList", ""))

    async def insert(self, after_id: int, track: PlaylistTrack) -> int:
        """Insert a track after the given id and return the new id."""
        result = await self._call(
            self.playlist,
            "Insert",
            AfterId=after_id,
            Uri=track.uri,
            Metadata=track.metadata,
        )
        return int(result["NewId"])

    async def delete_all(self) -> None:
        """Clear the playlist."""
        await self._call(self.playlist, "DeleteAll")

    async def delete_id(self, track_id: int) -> None:
        """Remove a single track."""
        await self._call(self.playlist, "DeleteId", Value=track_id)

    async def seek_id(self, track_id: int) -> None:
        """Jump to the given track."""
        await self._call(self.playlist, "SeekId", Value=track_id)

    async def seek_second(self, seconds: int) -> None:
        """Seek inside the current track."""
        await self._call(self.playlist, "SeekSecondAbsolute", Value=max(0, seconds))

    async def play(self) -> None:
        """Start playback."""
        await self._call(self.playlist, "Play")

    async def pause(self) -> None:
        """Pause playback."""
        await self._call(self.playlist, "Pause")

    async def stop(self) -> None:
        """Stop playback."""
        await self._call(self.playlist, "Stop")

    async def next(self) -> None:
        """Next track."""
        await self._call(self.playlist, "Next")

    async def previous(self) -> None:
        """Previous track."""
        await self._call(self.playlist, "Previous")

    async def transport_state(self) -> str:
        """Return Playing, Paused, Stopped or Buffering."""
        return str((await self._call(self.playlist, "TransportState"))["Value"])

    async def shuffle(self) -> bool:
        """Return the shuffle state."""
        return bool((await self._call(self.playlist, "Shuffle"))["Value"])

    async def set_shuffle(self, value: bool) -> None:
        """Set shuffle."""
        await self._call(self.playlist, "SetShuffle", Value=value)

    async def repeat(self) -> bool:
        """Return the repeat state."""
        return bool((await self._call(self.playlist, "Repeat"))["Value"])

    async def set_repeat(self, value: bool) -> None:
        """Set repeat."""
        await self._call(self.playlist, "SetRepeat", Value=value)

    # ------------------------------------------------------------------ product
    async def sources(self, refresh: bool = False) -> list[Source]:
        """Return the list of sources of the product."""
        if self._sources is None or refresh:
            if not self.has(self.product, "SourceXml"):
                self._sources = []
            else:
                xml = (await self._call(self.product, "SourceXml"))["Value"]
                self._sources = parse_source_xml(xml)
        return self._sources

    async def source_index(self) -> int | None:
        """Return the index of the active source."""
        if not self.has(self.product, "SourceIndex"):
            return None
        return int((await self._call(self.product, "SourceIndex"))["Value"])

    async def set_source_index(self, index: int) -> None:
        """Select a source by index."""
        await self._call(self.product, "SetSourceIndex", Value=index)

    async def playlist_source(self) -> Source | None:
        """Return the playlist source of the product."""
        for source in await self.sources():
            if source.type.lower() == "playlist":
                return source
        return None

    async def ensure_playlist_source(self) -> None:
        """Switch the product to its playlist source (and out of standby)."""
        if self.has(self.product, "Standby") and await self.standby():
            await self.set_standby(False)
        source = await self.playlist_source()
        if source is None:
            return
        if await self.source_index() != source.index:
            await self.set_source_index(source.index)

    async def standby(self) -> bool:
        """Return True if the product is in standby."""
        if not self.has(self.product, "Standby"):
            return False
        return bool((await self._call(self.product, "Standby"))["Value"])

    async def set_standby(self, value: bool) -> None:
        """Set standby."""
        await self._call(self.product, "SetStandby", Value=value)

    # ------------------------------------------------------------------- volume
    @property
    def has_volume(self) -> bool:
        """Return True if the volume can be controlled."""
        return self.has(self.volume, "SetVolume")

    async def volume_level(self) -> int:
        """Return the current volume."""
        return int((await self._call(self.volume, "Volume"))["Value"])

    async def volume_max(self) -> int:
        """Return the maximum volume."""
        if self.has(self.volume, "VolumeMax"):
            return int((await self._call(self.volume, "VolumeMax"))["Value"])
        if self.has(self.volume, "Characteristics"):
            result = await self._call(self.volume, "Characteristics")
            return int(result.get("VolumeMax", 100))
        return 100

    async def set_volume_level(self, value: int) -> None:
        """Set the volume."""
        await self._call(self.volume, "SetVolume", Value=value)

    async def mute(self) -> bool:
        """Return the mute state."""
        return bool((await self._call(self.volume, "Mute"))["Value"])

    async def set_mute(self, value: bool) -> None:
        """Set mute."""
        await self._call(self.volume, "SetMute", Value=value)

    # ------------------------------------------------------------- info / time
    async def current_track(self) -> tuple[str, str] | None:
        """Return uri and metadata of the currently playing track (any source)."""
        if not self.has(self.info, "Track"):
            return None
        result = await self._call(self.info, "Track")
        return str(result.get("Uri") or ""), str(result.get("Metadata") or "")

    async def details(self) -> dict[str, Any] | None:
        """Stream details of the current track (codec, bit rate, resolution)."""
        if not self.has(self.info, "Details"):
            return None
        result = await self._call(self.info, "Details")
        return {
            "codec": str(result.get("CodecName") or "") or None,
            "bit_rate": int(result.get("BitRate") or 0) or None,  # bit/s
            "bit_depth": int(result.get("BitDepth") or 0) or None,
            "sample_rate": int(result.get("SampleRate") or 0) or None,
            "lossless": bool(result.get("Lossless")) if "Lossless" in result else None,
        }

    async def position(self) -> tuple[int, int] | None:
        """Return (duration, seconds) of the current track."""
        if not self.has(self.time, "Time"):
            return None
        result = await self._call(self.time, "Time")
        return int(result.get("Duration", 0)), int(result.get("Seconds", 0))

    # -------------------------------------------------------------------- radio
    @property
    def has_radio(self) -> bool:
        """Return True if the renderer has the OpenHome Radio service."""
        return self.has(self.radio, "SetChannel")

    async def _ensure_source(self, source_type: str) -> None:
        if self.has(self.product, "Standby") and await self.standby():
            await self.set_standby(False)
        for source in await self.sources():
            if source.type.lower() == source_type:
                if await self.source_index() != source.index:
                    await self.set_source_index(source.index)
                return

    async def play_radio(self, uri: str, metadata: str) -> None:
        """Play a stream on the radio source (the playlist stays untouched)."""
        await self._ensure_source("radio")
        await self._call(self.radio, "SetChannel", Uri=uri, Metadata=metadata)
        await self._call(self.radio, "Play")

    # ----------------------------------------------------------- high level ops
    async def move(self, track_id: int, after_id: int) -> int:
        """Move a track behind another one (0 = to the top).

        OpenHome has no move action: the track is read, deleted and inserted
        again, which gives it a new id. If it was playing, playback continues
        with the moved track (from its beginning).
        """
        if track_id == after_id:
            return track_id
        entries = await self.read_list([track_id])
        if not entries:
            raise OpenHomeError(f"Track {track_id} is not in the playlist")
        entry = entries[0]
        was_current = await self.current_id() == track_id
        state = (await self.transport_state()).lower() if was_current else ""
        await self.delete_id(track_id)
        new_id = await self.insert(after_id, PlaylistTrack(entry.uri, entry.metadata))
        if was_current and state in ("playing", "buffering"):
            await self.seek_id(new_id)
            await self.play()
        return new_id

    async def load_tracks(
        self,
        tracks: list[PlaylistTrack],
        mode: str,
        start_playback: bool | None = None,
    ) -> int:
        """Load tracks into the playlist.

        mode:
          replace - clear playlist, insert, start with first new track
          add     - append to the end of the playlist
          next    - insert after the current track
          play    - insert after the current track and play the first new one

        Returns the number of tracks inserted.
        """
        if not tracks:
            return 0

        await self.ensure_playlist_source()
        capacity = await self.tracks_max()

        if mode == "replace":
            await self.delete_all()
            existing: list[int] = []
            after_id = 0
        else:
            existing = await self.ids()
            if mode == "add":
                after_id = existing[-1] if existing else 0
            else:
                current = await self.current_id()
                if current in existing:
                    after_id = current
                else:
                    after_id = existing[-1] if existing else 0

        free = max(0, capacity - len(existing))
        if len(tracks) > free:
            _LOGGER.warning(
                "Playlist of %s can hold only %s more tracks, %s of %s are loaded",
                self.name,
                free,
                free,
                len(tracks),
            )
            tracks = tracks[:free]

        first_id: int | None = None
        for track in tracks:
            after_id = await self.insert(after_id, track)
            if first_id is None:
                first_id = after_id

        if start_playback is None:
            start_playback = mode in ("replace", "play")
        if first_id is not None and start_playback:
            await self.seek_id(first_id)
            await self.play()
        return len(tracks)
