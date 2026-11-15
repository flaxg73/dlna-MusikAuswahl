"""Media player entity for a Linn DSM with DLNA library browsing."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from urllib.parse import quote, unquote, urlparse

import voluptuous as vol
from async_upnp_client.exceptions import UpnpError
from didl_lite import didl_lite
from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_MEDIA_ENQUEUE,
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaPlayerEnqueue,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
    SearchMedia,
    SearchMediaQuery,
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import MusikConfigEntry, RuntimeData, async_connect_renderer
from .const import (
    ATTR_MODE,
    ATTR_PLAY,
    BROWSE_PAGE_SIZE,
    DOMAIN,
    LOAD_MODES,
    MODE_ADD,
    MODE_NEXT,
    MODE_PLAY,
    MODE_REPLACE,
    SERVICE_LOAD_MEDIA,
    SERVICE_MOVE_TRACK,
    SERVICE_PLAY_TRACK,
    SERVICE_REMOVE_TRACKS,
    URI_SCHEME,
    URI_SCHEME_QUEUE,
)
from .didl import (
    album_art_uri,
    is_container,
    is_playable_audio_item,
    parse_duration,
    parse_metadata,
    upnp_class_of,
)
from .dms import MediaServer
from .openhome import OpenHomeRenderer, PlaylistTrack
from .playlist import PlaylistCache, QueueEntry
from .upnp import CONNECT_ERRORS

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=5)
PARALLEL_UPDATES = 1
SEARCH_LIMIT = 100
LIBRARY_PAGE_SIZE = 100

_ENQUEUE_TO_MODE = {
    MediaPlayerEnqueue.REPLACE: MODE_REPLACE,
    MediaPlayerEnqueue.ADD: MODE_ADD,
    MediaPlayerEnqueue.NEXT: MODE_NEXT,
    MediaPlayerEnqueue.PLAY: MODE_PLAY,
}

_STATE_MAP = {
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "stopped": MediaPlayerState.IDLE,
    "buffering": MediaPlayerState.BUFFERING,
}


# --------------------------------------------------------------------------- ids
def make_content_id(
    udn: str, object_id: str, start: int = 0, parent_id: str | None = None
) -> str:
    """Build a media_content_id for an object on a media server.

    For items the id of the parent container is appended, so that the item
    can be looked up in the listing it was selected from.
    """
    content_id = f"{URI_SCHEME}://{quote(udn, safe='')}/{quote(object_id, safe='')}"
    if start:
        content_id += f"/{start}"
    if parent_id:
        content_id += f"?parent={quote(parent_id, safe='')}"
    return content_id


def content_parent(content_id: str) -> str | None:
    """Return the parent container id stored in a media_content_id."""
    _, _, query = content_id.partition("?")
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key == "parent" and value:
            return unquote(value)
    return None


def parse_content_id(content_id: str) -> tuple[str, str, int]:
    """Split a media_content_id into server UDN, object id and page start."""
    prefix = f"{URI_SCHEME}://"
    if not content_id.startswith(prefix):
        raise ValueError(f"Not a {DOMAIN} media id: {content_id}")
    parts = content_id[len(prefix) :].partition("?")[0].split("/")
    if len(parts) < 2 or not parts[0]:
        raise ValueError(f"Invalid media id: {content_id}")
    start = int(parts[2]) if len(parts) > 2 and parts[2] else 0
    return unquote(parts[0]), unquote(parts[1]), start


def media_class_for(obj: didl_lite.DidlObject) -> MediaClass:
    """Map an UPnP class onto a Home Assistant media class."""
    upnp_class = upnp_class_of(obj)
    if upnp_class.startswith("object.container.album"):
        return MediaClass.ALBUM
    if upnp_class.startswith("object.container.person"):
        return MediaClass.ARTIST
    if upnp_class.startswith("object.container.genre"):
        return MediaClass.GENRE
    if upnp_class.startswith("object.container.playlistcontainer"):
        return MediaClass.PLAYLIST
    if upnp_class.startswith("object.container"):
        return MediaClass.DIRECTORY
    if upnp_class.startswith("object.item.audioitem"):
        return MediaClass.TRACK
    return MediaClass.MUSIC


# ------------------------------------------------------------------------ setup
async def async_setup_entry(
    hass: HomeAssistant,
    entry: MusikConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the media player."""
    async_add_entities([LinnPlaylistPlayer(entry)], update_before_add=True)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_LOAD_MEDIA,
        {
            vol.Required("media_content_id"): cv.string,
            vol.Optional(ATTR_MODE, default=MODE_REPLACE): vol.In(LOAD_MODES),
            vol.Optional(ATTR_PLAY): cv.boolean,
        },
        "async_load_media",
    )
    platform.async_register_entity_service(
        SERVICE_PLAY_TRACK,
        {vol.Required("track_id"): vol.Coerce(int)},
        "async_play_track",
    )
    platform.async_register_entity_service(
        SERVICE_REMOVE_TRACKS,
        {vol.Required("track_ids"): vol.All(cv.ensure_list, [vol.Coerce(int)])},
        "async_remove_tracks",
    )
    platform.async_register_entity_service(
        SERVICE_MOVE_TRACK,
        {
            vol.Required("track_id"): vol.Coerce(int),
            vol.Required("position"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        },
        "async_move_track",
    )


class LinnPlaylistPlayer(MediaPlayerEntity):
    """The Linn DSM, with the DLNA libraries as browsable media."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, entry: MusikConfigEntry) -> None:
        """Initialise the entity."""
        self._entry = entry
        data: RuntimeData = entry.runtime_data
        self._renderer: OpenHomeRenderer | None = data.renderer
        self._servers: dict[str, MediaServer] = data.servers
        self._max_tracks = data.max_tracks
        device = data.renderer.device
        self._attr_unique_id = data.renderer.udn
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, data.renderer.udn)},
            name=entry.title,
            manufacturer=device.manufacturer,
            model=device.model_name,
            sw_version=device.model_number,
            serial_number=device.serial_number,
        )
        self._attr_supported_features = self._features(data.renderer)
        self._source_names: dict[str, int] = {}
        self._last_track_key: tuple[str, str] | None = None
        self._playlist_active = True
        self._queue = PlaylistCache()
        self._current_id = 0

    async def async_added_to_hass(self) -> None:
        """Make the entity reachable for the websocket API of the card."""
        self.hass.data.setdefault(DOMAIN, {})[self.entity_id] = self

    async def async_will_remove_from_hass(self) -> None:
        """Unregister."""
        self.hass.data.get(DOMAIN, {}).pop(self.entity_id, None)

    # ------------------------------------------------------------- features
    @staticmethod
    def _features(renderer: OpenHomeRenderer) -> MediaPlayerEntityFeature:
        features = (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
            | MediaPlayerEntityFeature.PLAY_MEDIA
            | MediaPlayerEntityFeature.BROWSE_MEDIA
            | MediaPlayerEntityFeature.MEDIA_ENQUEUE
            | MediaPlayerEntityFeature.CLEAR_PLAYLIST
            | MediaPlayerEntityFeature.SHUFFLE_SET
            | MediaPlayerEntityFeature.REPEAT_SET
            | MediaPlayerEntityFeature.SEARCH_MEDIA
        )
        if renderer.has(renderer.playlist, "SeekSecondAbsolute"):
            features |= MediaPlayerEntityFeature.SEEK
        if renderer.has(renderer.product, "SetStandby"):
            features |= MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF
        if renderer.has(renderer.product, "SetSourceIndex"):
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        if renderer.has_volume:
            features |= (
                MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_STEP
                | MediaPlayerEntityFeature.VOLUME_MUTE
            )
        return features

    async def _async_renderer(self) -> OpenHomeRenderer:
        if self._renderer is None:
            self._renderer = await async_connect_renderer(self.hass, self._entry)
        return self._renderer

    async def _async_call(self, func_name: str, *args: Any) -> None:
        try:
            renderer = await self._async_renderer()
            await getattr(renderer, func_name)(*args)
        except CONNECT_ERRORS as err:
            self._renderer = None
            raise HomeAssistantError(f"Linn renderer: {func_name} failed: {err}") from err
        self.async_schedule_update_ha_state(True)

    # --------------------------------------------------------------- update
    async def async_update(self) -> None:
        """Poll the renderer."""
        try:
            await self._async_poll(await self._async_renderer())
        except CONNECT_ERRORS as err:
            if self._attr_available:
                _LOGGER.warning("Lost connection to %s: %s", self._entry.title, err)
            self._attr_available = False
            self._renderer = None
            return
        self._attr_available = True

    async def _async_poll(self, renderer: OpenHomeRenderer) -> None:
        # the playlist is kept by the Linn, also in standby
        await self._queue.refresh(renderer)
        self._current_id = await renderer.current_id()

        if await renderer.standby():
            self._attr_state = MediaPlayerState.OFF
            self._clear_media()
            return

        # sources
        sources = await renderer.sources()
        self._source_names = {s.name: s.index for s in sources if s.visible}
        self._attr_source_list = list(self._source_names) or None
        index = await renderer.source_index()
        active = next((s for s in sources if s.index == index), None)
        self._attr_source = active.name if active else None
        self._playlist_active = active is None or active.type.lower() == "playlist"

        if self._playlist_active:
            transport = (await renderer.transport_state()).lower()
            self._attr_state = _STATE_MAP.get(transport, MediaPlayerState.ON)
            self._attr_shuffle = await renderer.shuffle()
            self._attr_repeat = RepeatMode.ALL if await renderer.repeat() else RepeatMode.OFF
        else:
            self._attr_state = MediaPlayerState.ON

        # current track
        track: tuple[str, str] | None = await renderer.current_track()
        if track is None and self._playlist_active:
            current = await renderer.current_id()
            entries = await renderer.read_list([current]) if current else []
            track = (entries[0].uri, entries[0].metadata) if entries else None
        self._update_track(track)

        position = await renderer.position()
        if position is not None and track and track[0]:
            duration, seconds = position
            self._attr_media_duration = duration or self._attr_media_duration
            self._attr_media_position = seconds
            self._attr_media_position_updated_at = dt_util.utcnow()

        if renderer.has_volume:
            maximum = await renderer.volume_max() or 100
            self._attr_volume_level = min(1.0, await renderer.volume_level() / maximum)
            self._attr_is_volume_muted = await renderer.mute()

    def _clear_media(self) -> None:
        self._last_track_key = None
        self._attr_media_title = None
        self._attr_media_artist = None
        self._attr_media_album_name = None
        self._attr_media_album_artist = None
        self._attr_media_track = None
        self._attr_media_image_url = None
        self._attr_media_content_id = None
        self._attr_media_duration = None
        self._attr_media_position = None
        self._attr_media_position_updated_at = None

    def _update_track(self, track: tuple[str, str] | None) -> None:
        if not track or not (track[0] or track[1]):
            self._clear_media()
            return
        if track == self._last_track_key:
            return
        self._clear_media()
        self._last_track_key = track
        uri, metadata = track
        self._attr_media_content_id = uri
        obj = parse_metadata(metadata)
        if obj is None:
            return
        self._attr_media_title = getattr(obj, "title", None)
        self._attr_media_artist = getattr(obj, "artist", None) or getattr(obj, "creator", None)
        self._attr_media_album_name = getattr(obj, "album", None)
        self._attr_media_album_artist = getattr(obj, "album_artist", None)
        track_number = getattr(obj, "original_track_number", None)
        try:
            self._attr_media_track = int(track_number) if track_number else None
        except ValueError:
            self._attr_media_track = None
        self._attr_media_image_url = album_art_uri(obj)
        for res in getattr(obj, "res", None) or []:
            if duration := parse_duration(res.duration):
                self._attr_media_duration = duration
                break

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose servers and a summary of the playlist (the full list is in the card)."""
        index = self._queue.index_of(self._current_id)
        return {
            "media_servers": [server.name for server in self._servers.values()],
            "playlist_length": len(self._queue),
            "playlist_version": self._queue.version,
            "playlist_position": index + 1 if index is not None else None,
            "current_track_id": self._current_id or None,
        }

    # -------------------------------------------------------------- playlist
    def _art_url(self, art: str | None) -> str | None:
        """Cover URL proxied through Home Assistant (avoids mixed content)."""
        if not art or not self._is_server_host(art):
            return None
        return self.get_browse_image_url("cover", "cover", art)

    def _is_server_host(self, url: str) -> bool:
        host = urlparse(url).hostname
        return host is not None and any(s.host == host for s in self._servers.values())

    def _queue_entry_dict(self, entry: QueueEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "title": entry.title,
            "artist": entry.artist,
            "album": entry.album,
            "duration": entry.duration,
            "image": self._art_url(entry.art),
        }

    async def async_playlist_payload(self, refresh: bool = False) -> dict[str, Any]:
        """Playlist for the Lovelace card (websocket API)."""
        if refresh:
            try:
                renderer = await self._async_renderer()
                await self._queue.refresh(renderer)
                self._current_id = await renderer.current_id()
            except CONNECT_ERRORS as err:
                self._renderer = None
                raise HomeAssistantError(f"Linn renderer not reachable: {err}") from err
        return {
            "version": self._queue.version,
            "current_id": self._current_id or None,
            "state": self.state,
            "playlist_active": self._playlist_active,
            "tracks": [self._queue_entry_dict(e) for e in self._queue.ordered()],
        }

    async def _async_queue_op(self, func) -> None:
        try:
            renderer = await self._async_renderer()
            await func(renderer)
            await self._queue.refresh(renderer)
            self._current_id = await renderer.current_id()
        except CONNECT_ERRORS as err:
            self._renderer = None
            raise HomeAssistantError(f"Linn renderer: {err}") from err
        self.async_schedule_update_ha_state(True)

    def _check_track(self, track_id: int) -> None:
        if self._queue.index_of(track_id) is None:
            raise ServiceValidationError(f"Track {track_id} is not in the Linn playlist")

    async def async_play_track(self, track_id: int) -> None:
        """Service: play a track of the playlist."""
        self._check_track(track_id)

        async def _play(renderer: OpenHomeRenderer) -> None:
            await renderer.ensure_playlist_source()
            await renderer.seek_id(track_id)
            await renderer.play()

        await self._async_queue_op(_play)

    async def async_remove_tracks(self, track_ids: list[int]) -> None:
        """Service: remove tracks from the playlist."""

        async def _remove(renderer: OpenHomeRenderer) -> None:
            for track_id in track_ids:
                if self._queue.index_of(track_id) is not None:
                    await renderer.delete_id(track_id)

        await self._async_queue_op(_remove)

    async def async_move_track(self, track_id: int, position: int) -> None:
        """Service: move a track to a position (1 = top)."""
        self._check_track(track_id)
        others = [i for i in self._queue.ids if i != track_id]
        position = min(position, len(others) + 1)
        after_id = others[position - 2] if position > 1 else 0

        async def _move(renderer: OpenHomeRenderer) -> None:
            await renderer.move(track_id, after_id)

        await self._async_queue_op(_move)

    # ------------------------------------------------------------- controls
    async def async_media_play(self) -> None:
        """Play."""
        await self._async_call("play")

    async def async_media_pause(self) -> None:
        """Pause."""
        await self._async_call("pause")

    async def async_media_stop(self) -> None:
        """Stop."""
        await self._async_call("stop")

    async def async_media_next_track(self) -> None:
        """Next track."""
        await self._async_call("next")

    async def async_media_previous_track(self) -> None:
        """Previous track."""
        await self._async_call("previous")

    async def async_media_seek(self, position: float) -> None:
        """Seek."""
        await self._async_call("seek_second", int(position))

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Shuffle on/off."""
        await self._async_call("set_shuffle", shuffle)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Repeat on/off (the Linn playlist only knows 'all')."""
        await self._async_call("set_repeat", repeat != RepeatMode.OFF)

    async def async_clear_playlist(self) -> None:
        """Clear the playlist of the Linn."""
        await self._async_call("delete_all")

    async def async_turn_on(self) -> None:
        """Leave standby."""
        await self._async_call("set_standby", False)

    async def async_turn_off(self) -> None:
        """Enter standby."""
        await self._async_call("set_standby", True)

    async def async_select_source(self, source: str) -> None:
        """Select an input."""
        if source not in self._source_names:
            renderer = await self._async_renderer()
            self._source_names = {
                s.name: s.index for s in await renderer.sources(refresh=True) if s.visible
            }
        if source not in self._source_names:
            raise ServiceValidationError(f"Unknown source {source}")
        await self._async_call("set_source_index", self._source_names[source])

    async def async_set_volume_level(self, volume: float) -> None:
        """Set the volume (0..1)."""
        renderer = await self._async_renderer()
        maximum = await renderer.volume_max() or 100
        await self._async_call("set_volume_level", round(volume * maximum))

    async def async_volume_up(self) -> None:
        """Volume +1."""
        renderer = await self._async_renderer()
        await self._async_call("set_volume_level", await renderer.volume_level() + 1)

    async def async_volume_down(self) -> None:
        """Volume -1."""
        renderer = await self._async_renderer()
        await self._async_call(
            "set_volume_level", max(0, await renderer.volume_level() - 1)
        )

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute."""
        await self._async_call("set_mute", mute)

    # --------------------------------------------------------- play / load
    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Load media into the Linn playlist."""
        if media_id.startswith(f"{URI_SCHEME_QUEUE}://"):
            try:
                track_id = int(media_id[len(URI_SCHEME_QUEUE) + 3 :])
            except ValueError as err:
                raise ServiceValidationError(f"Invalid playlist id {media_id}") from err
            await self.async_play_track(track_id)
            return
        enqueue = kwargs.get(ATTR_MEDIA_ENQUEUE)
        mode = _ENQUEUE_TO_MODE.get(enqueue, MODE_REPLACE) if enqueue else MODE_REPLACE
        await self.async_load_media(media_id, mode, media_type=media_type)

    async def async_load_media(
        self,
        media_content_id: str,
        mode: str = MODE_REPLACE,
        play: bool | None = None,
        media_type: str | None = None,
    ) -> None:
        """Service: load a track, album, folder or playlist into the Linn playlist."""
        await self.async_load(media_content_id, mode, play, media_type)

    async def async_load(
        self,
        media_content_id: str,
        mode: str = MODE_REPLACE,
        play: bool | None = None,
        media_type: str | None = None,
    ) -> int:
        """Load media into the playlist and return the number of tracks."""
        tracks = await self._async_tracks_for(media_content_id, media_type)
        if not tracks:
            raise ServiceValidationError(
                f"No playable audio tracks found for {media_content_id}"
            )
        try:
            renderer = await self._async_renderer()
            count = await renderer.load_tracks(tracks, mode, play)
        except CONNECT_ERRORS as err:
            self._renderer = None
            raise HomeAssistantError(f"Loading the playlist failed: {err}") from err
        _LOGGER.debug("Loaded %s tracks (%s) into %s", count, mode, self._entry.title)
        self.async_schedule_update_ha_state(True)
        return count

    async def _async_tracks_for(
        self, media_id: str, media_type: str | None = None
    ) -> list[PlaylistTrack]:
        if media_id.startswith(f"{URI_SCHEME}://"):
            try:
                udn, object_id, _start = parse_content_id(media_id)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
            server = self._server(udn)
            try:
                return await server.async_collect_tracks(
                    object_id,
                    self._max_tracks,
                    parent_id=content_parent(media_id),
                    is_item=True if media_type == MediaType.TRACK else None,
                )
            except CONNECT_ERRORS as err:
                raise HomeAssistantError(
                    f"Media server {server.name} not reachable: {err}"
                ) from err

        # Anything else: media source items or plain URLs, loaded as single track
        title = media_id.rsplit("/", 1)[-1]
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = item.url
        url = async_process_play_media_url(self.hass, media_id)
        metadata = didl_lite.to_xml_string(
            didl_lite.MusicTrack(
                id="0",
                parent_id="-1",
                title=title,
                restricted="1",
                res=[didl_lite.Resource(url, "http-get:*:audio/*:*")],
            )
        ).decode()
        return [PlaylistTrack(uri=url, metadata=metadata)]

    def _server(self, udn: str) -> MediaServer:
        if udn not in self._servers:
            raise ServiceValidationError(f"Media server {udn} is not configured")
        return self._servers[udn]

    # ---------------------------------------------------------------- browse
    def _browse_item(
        self,
        server: MediaServer,
        obj: didl_lite.DidlObject,
        parent_id: str | None = None,
    ) -> BrowseMedia:
        container = is_container(obj)
        content_id = make_content_id(
            server.udn,
            obj.id,
            parent_id=None if container else (parent_id or obj.parent_id or None),
        )
        thumbnail = None
        if art := server.absolute_url(album_art_uri(obj)):
            thumbnail = self.get_browse_image_url(MediaType.MUSIC, content_id, art)
        return BrowseMedia(
            media_class=media_class_for(obj),
            media_content_id=content_id,
            media_content_type=MediaType.PLAYLIST if container else MediaType.TRACK,
            title=str(getattr(obj, "title", None) or obj.id),
            can_play=True,
            can_expand=container,
            thumbnail=thumbnail,
        )

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Browse the configured DLNA servers."""
        if not media_content_id or media_content_id == f"{URI_SCHEME}://":
            return self._browse_root()
        if media_content_id.startswith(f"{URI_SCHEME_QUEUE}://"):
            return await self._async_browse_queue()
        try:
            udn, object_id, start = parse_content_id(media_content_id)
        except ValueError as err:
            raise BrowseError(str(err)) from err
        server = self._servers.get(udn)
        if server is None:
            raise BrowseError(f"Media server {udn} is not configured")
        try:
            return await self._async_browse_container(server, object_id, start)
        except CONNECT_ERRORS as err:
            raise BrowseError(f"Media server {server.name} not reachable: {err}") from err

    def _queue_folder(self) -> BrowseMedia:
        return BrowseMedia(
            media_class=MediaClass.PLAYLIST,
            media_content_id=f"{URI_SCHEME_QUEUE}://",
            media_content_type=MediaType.PLAYLIST,
            title=f"Aktuelle Linn-Playlist ({len(self._queue)} Titel)",
            can_play=False,
            can_expand=True,
        )

    async def _async_browse_queue(self) -> BrowseMedia:
        try:
            await self.async_playlist_payload(refresh=True)
        except HomeAssistantError as err:
            raise BrowseError(str(err)) from err
        children = []
        for number, entry in enumerate(self._queue.ordered(), start=1):
            marker = "▶ " if entry.id == self._current_id else ""
            subtitle = " – ".join(x for x in (entry.artist, entry.album) if x)
            image = self._art_url(entry.art)
            children.append(
                BrowseMedia(
                    media_class=MediaClass.TRACK,
                    media_content_id=f"{URI_SCHEME_QUEUE}://{entry.id}",
                    media_content_type=MediaType.TRACK,
                    title=f"{marker}{number}. {entry.title}"
                    + (f" ({subtitle})" if subtitle else ""),
                    can_play=True,
                    can_expand=False,
                    thumbnail=image,
                )
            )
        folder = self._queue_folder()
        folder.children = children
        folder.children_media_class = MediaClass.TRACK
        return folder

    def _browse_root(self) -> BrowseMedia:
        children = [self._queue_folder()] + [
            BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id=make_content_id(server.udn, "0"),
                media_content_type=MediaType.PLAYLIST,
                title=server.name,
                can_play=False,
                can_expand=True,
            )
            for server in self._servers.values()
        ]
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=f"{URI_SCHEME}://",
            media_content_type=MediaType.PLAYLIST,
            title="DLNA Musikserver",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    async def _async_browse_container(
        self, server: MediaServer, object_id: str, start: int
    ) -> BrowseMedia:
        if object_id == "0":
            title = server.name
            container_class = MediaClass.DIRECTORY
            thumbnail = None
        else:
            title, container_class, thumbnail = object_id, MediaClass.DIRECTORY, None
            try:
                meta = await server.async_metadata(object_id)
            except (*CONNECT_ERRORS, AssertionError, IndexError) as err:
                # some servers answer BrowseMetadata badly; browsing still works
                _LOGGER.debug("BrowseMetadata of %s failed: %s", object_id, err)
                meta = None
            if meta is not None:
                title = str(getattr(meta, "title", None) or object_id)
                if is_container(meta):
                    container_class = media_class_for(meta)
            if meta is not None and (art := server.absolute_url(album_art_uri(meta))):
                thumbnail = self.get_browse_image_url(
                    MediaType.MUSIC, make_content_id(server.udn, object_id), art
                )

        page = await server.async_browse(object_id, start, BROWSE_PAGE_SIZE)
        children = [
            self._browse_item(server, child, object_id)
            for child in page.children
            if is_container(child) or is_playable_audio_item(child)
        ]
        next_start = start + len(page.children)
        if page.children and next_start < page.total:
            children.append(
                BrowseMedia(
                    media_class=MediaClass.DIRECTORY,
                    media_content_id=make_content_id(server.udn, object_id, next_start),
                    media_content_type=MediaType.PLAYLIST,
                    title=(
                        f"Weitere … ({next_start + 1}–"
                        f"{min(next_start + BROWSE_PAGE_SIZE, page.total)} von {page.total})"
                    ),
                    can_play=False,
                    can_expand=True,
                )
            )
        if start:
            title = f"{title} ({start + 1}–{next_start})"

        return BrowseMedia(
            media_class=container_class,
            media_content_id=make_content_id(server.udn, object_id, start),
            media_content_type=MediaType.PLAYLIST,
            title=title,
            can_play=object_id != "0",
            can_expand=True,
            children=children,
            thumbnail=thumbnail,
        )

    async def async_get_browse_image(
        self,
        media_content_type: MediaType | str,
        media_content_id: str,
        media_image_id: str | None = None,
    ) -> tuple[bytes | None, str | None]:
        """Fetch cover art for the media browser through Home Assistant."""
        if not media_image_id:
            return None, None
        if media_content_type == "cover":
            # playlist covers: only from configured media servers
            if not self._is_server_host(media_image_id):
                return None, None
            return await self._async_fetch_image(media_image_id)
        try:
            udn, _object_id, _start = parse_content_id(media_content_id)
        except ValueError:
            return None, None
        server = self._servers.get(udn)
        host = urlparse(media_image_id).hostname
        # only proxy images served by the media server itself
        if server is None or host is None or host != server.host:
            return None, None
        return await self._async_fetch_image(media_image_id)

    # ---------------------------------------------------------------- search
    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        """Search the DLNA servers."""
        servers: list[tuple[MediaServer, str]]
        if query.media_content_id and query.media_content_id.startswith(f"{URI_SCHEME}://"):
            try:
                udn, object_id, _start = parse_content_id(query.media_content_id)
            except ValueError as err:
                raise BrowseError(str(err)) from err
            servers = [(self._server(udn), object_id)]
        else:
            servers = [(server, "0") for server in self._servers.values()]

        results: list[BrowseMedia] = []
        for server, obj in await self._async_search_objects(
            servers, query.search_query
        ):
            item = self._browse_item(server, obj)
            if query.media_filter_classes and item.media_class not in query.media_filter_classes:
                continue
            results.append(item)
        return SearchMedia(result=results)

    async def _async_search_objects(
        self, servers: list[tuple[MediaServer, str]], text: str
    ) -> list[tuple[MediaServer, didl_lite.DidlObject]]:
        found: list[tuple[MediaServer, didl_lite.DidlObject]] = []
        for server, container_id in servers:
            try:
                objects = await server.async_search(container_id, text, SEARCH_LIMIT)
            except CONNECT_ERRORS as err:
                _LOGGER.warning("Search on %s failed: %s", server.name, err)
                continue
            found += [
                (server, obj)
                for obj in objects
                if is_container(obj) or is_playable_audio_item(obj)
            ]
        return found

    # --------------------------------------------------------- library card
    def _library_item(
        self, server: MediaServer, obj: didl_lite.DidlObject, parent_id: str | None = None
    ) -> dict[str, Any]:
        container = is_container(obj)
        duration = None
        if not container:
            for res in getattr(obj, "res", None) or []:
                if duration := parse_duration(res.duration):
                    break
        track = getattr(obj, "original_track_number", None)
        return {
            "id": make_content_id(
                server.udn,
                obj.id,
                parent_id=None if container else (parent_id or obj.parent_id or None),
            ),
            "title": str(getattr(obj, "title", None) or obj.id),
            "subtitle": getattr(obj, "artist", None) or getattr(obj, "creator", None),
            "album": getattr(obj, "album", None),
            "kind": str(media_class_for(obj)),
            "container": container,
            "image": self._art_url(server.absolute_url(album_art_uri(obj))),
            "duration": duration,
            "track": int(track) if str(track or "").isdigit() else None,
            "count": int(getattr(obj, "child_count", None) or 0) or None,
        }

    async def async_library_browse(
        self, media_content_id: str | None, start: int = 0
    ) -> dict[str, Any]:
        """Browse payload for the library card."""
        if not media_content_id:
            return {
                "id": None,
                "title": None,
                "subtitle": None,
                "kind": "directory",
                "image": None,
                "can_play": False,
                "children": [
                    {
                        "id": make_content_id(server.udn, "0"),
                        "title": server.name,
                        "subtitle": None,
                        "kind": "server",
                        "container": True,
                        "image": None,
                    }
                    for server in self._servers.values()
                ],
                "total": len(self._servers),
                "next_start": None,
            }
        try:
            udn, object_id, _start = parse_content_id(media_content_id)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        server = self._server(udn)
        title, subtitle, image, kind = server.name, None, None, "directory"
        try:
            if object_id != "0":
                try:
                    meta = await server.async_metadata(object_id)
                except (AssertionError, IndexError, UpnpError) as err:
                    _LOGGER.debug("BrowseMetadata of %s failed: %s", object_id, err)
                    meta = None
                if meta is not None:
                    title = str(getattr(meta, "title", None) or object_id)
                    subtitle = getattr(meta, "artist", None) or getattr(meta, "creator", None)
                    image = self._art_url(server.absolute_url(album_art_uri(meta)))
                    if is_container(meta):
                        kind = str(media_class_for(meta))
                else:
                    title = object_id
            page = await server.async_browse(object_id, start, LIBRARY_PAGE_SIZE)
        except CONNECT_ERRORS as err:
            raise HomeAssistantError(f"Media server {server.name} not reachable: {err}") from err
        children = [
            self._library_item(server, child, object_id)
            for child in page.children
            if is_container(child) or is_playable_audio_item(child)
        ]
        next_start = start + len(page.children)
        return {
            "id": make_content_id(server.udn, object_id),
            "title": title,
            "subtitle": subtitle,
            "kind": kind,
            "image": image,
            "server": server.name,
            "can_play": object_id != "0",
            "children": children,
            "total": page.total,
            "next_start": next_start if page.children and next_start < page.total else None,
        }

    async def async_library_search(
        self, text: str, media_content_id: str | None = None
    ) -> dict[str, Any]:
        """Search payload for the library card."""
        servers: list[tuple[MediaServer, str]]
        if media_content_id:
            try:
                udn, object_id, _start = parse_content_id(media_content_id)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
            servers = [(self._server(udn), object_id)]
        else:
            servers = [(server, "0") for server in self._servers.values()]
        found = await self._async_search_objects(servers, text)
        items = [self._library_item(server, obj) for server, obj in found]
        # albums/artists/folders first, then tracks
        items.sort(key=lambda item: not item["container"])
        return {"query": text, "children": items}
