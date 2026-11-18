"""Media player entity for a Linn DSM with DLNA library browsing."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlparse

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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    SERVICE_DELETE_PLAYLIST,
    SERVICE_LOAD_MEDIA,
    SERVICE_LOAD_PLAYLIST,
    SERVICE_MOVE_TRACK,
    SERVICE_PLAY_TRACK,
    SERVICE_REMOVE_TRACKS,
    SERVICE_SAVE_PLAYLIST,
    URI_SCHEME,
    URI_SCHEME_QUEUE,
    URI_SCHEME_RADIO,
)
from .didl import (
    album_art_uri,
    is_audio_class_item,
    is_container,
    is_playable_audio_item,
    parse_duration,
    parse_metadata,
    radio_metadata,
    upnp_class_of,
)
from .dms import MediaServer
from .openhome import OpenHomeRenderer, PlaylistTrack
from .playlist import PlaylistCache, QueueEntry
from .tunein import RadioEntry, TuneInClient, TuneInError
from .upnp import CONNECT_ERRORS

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=5)
PARALLEL_UPDATES = 1
SEARCH_LIMIT = 100
FAVORITES_TTL = 300
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
    """Map the UPnP class of an object onto a Home Assistant media class."""
    return media_class_for_upnp(upnp_class_of(obj))


def media_class_for_upnp(upnp_class: str) -> MediaClass:
    """Map an UPnP class onto a Home Assistant media class."""
    upnp_class = upnp_class.strip().lower()
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
    platform.async_register_entity_service(
        SERVICE_SAVE_PLAYLIST,
        {vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=80))},
        "async_save_playlist",
    )
    platform.async_register_entity_service(
        SERVICE_LOAD_PLAYLIST,
        {
            vol.Required("name"): cv.string,
            vol.Optional(ATTR_MODE, default=MODE_REPLACE): vol.In(LOAD_MODES),
            vol.Optional(ATTR_PLAY): cv.boolean,
        },
        "async_load_playlist",
    )
    platform.async_register_entity_service(
        SERVICE_DELETE_PLAYLIST,
        {vol.Required("name"): cv.string},
        "async_delete_playlist",
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
        self._qobuz_udn = data.qobuz_udn
        self._tunein_username = data.tunein_username
        self._saved = data.saved
        self._favorites_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._tunein: TuneInClient | None = None
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
        """Cover URL for the frontend.

        https images (Qobuz, TuneIn CDNs) are used directly; http images of
        the media servers are proxied through Home Assistant (mixed content).
        """
        if not art:
            return None
        if art.startswith("https://"):
            return art
        if not self._is_server_host(art):
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
        if media_content_id.startswith(f"{URI_SCHEME_RADIO}://"):
            return await self._async_play_radio(media_content_id, mode)
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
            title = server.title_for(object_id) or object_id
            container_class, thumbnail = MediaClass.DIRECTORY, None
            try:
                meta = await server.async_metadata(object_id)
            except (*CONNECT_ERRORS, AssertionError, IndexError) as err:
                # some servers answer BrowseMetadata badly; browsing still works
                _LOGGER.debug("BrowseMetadata of %s failed: %s", object_id, err)
                meta = None
            if meta is not None:
                title = server.title_for(object_id, getattr(meta, "title", None)) or object_id
                if is_container(meta):
                    container_class = media_class_for(meta)
            art = server.absolute_url(album_art_uri(meta)) if meta is not None else None
            art = art or server.details_for(object_id)[0]
            if art:
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
                # search results often come without <res>: loading such an
                # item works through its parent container
                if is_container(obj) or is_playable_audio_item(obj) or is_audio_class_item(obj)
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
        self, media_content_id: str | None, start: int = 0, source: str = "server"
    ) -> dict[str, Any]:
        """Browse payload for the library card."""
        if media_content_id and media_content_id.startswith(f"{URI_SCHEME_RADIO}://"):
            return await self._async_radio_browse(media_content_id)
        if not media_content_id and source == "radio":
            return await self._async_radio_browse(f"{URI_SCHEME_RADIO}://")
        if not media_content_id and source == "qobuz":
            return await self._async_qobuz_root(start)
        if not media_content_id:
            servers = [s for s in self._servers.values() if s.udn != self._qobuz_udn]
            servers = servers or list(self._servers.values())
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
                    for server in servers
                ],
                "total": len(servers),
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
                    title = server.title_for(object_id, getattr(meta, "title", None))
                    subtitle = getattr(meta, "artist", None) or getattr(meta, "creator", None)
                    image = self._art_url(server.absolute_url(album_art_uri(meta)))
                    if is_container(meta):
                        kind = str(media_class_for(meta))
                else:
                    title = server.title_for(object_id)
                # fill gaps (e.g. upmpdcli plugins) with what the listing showed
                cached_art, cached_artist, cached_class = server.details_for(object_id)
                image = image or self._art_url(cached_art)
                subtitle = subtitle or cached_artist
                if kind == "directory" and cached_class.lower().startswith("object.container."):
                    kind = str(media_class_for_upnp(cached_class))
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
        self, text: str, media_content_id: str | None = None, source: str = "server"
    ) -> dict[str, Any]:
        """Search payload for the library card."""
        if source == "radio":
            try:
                found = await self._tunein_client().search(text)
            except TuneInError as err:
                raise HomeAssistantError(str(err)) from err
            return {"query": text, "children": [self._radio_item(e) for e in found]}
        servers: list[tuple[MediaServer, str]]
        if media_content_id and media_content_id.startswith(f"{URI_SCHEME_RADIO}://"):
            media_content_id = None
        if not media_content_id and source == "qobuz":
            if not self._qobuz_udn or self._qobuz_udn not in self._servers:
                return {"query": text, "children": []}
            media_content_id = make_content_id(self._qobuz_udn, "0")
        if media_content_id:
            try:
                udn, object_id, _start = parse_content_id(media_content_id)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
            servers = [(self._server(udn), object_id)]
        else:
            servers = [
                (server, "0") for server in self._servers.values() if server.udn != self._qobuz_udn
            ] or [(server, "0") for server in self._servers.values()]
        found = await self._async_search_objects(servers, text)
        items = [self._library_item(server, obj) for server, obj in found]
        # albums/artists/folders first, then tracks
        items.sort(key=lambda item: not item["container"])
        return {"query": text, "children": items}

    # ----------------------------------------------------------------- qobuz
    async def _async_qobuz_root(self, start: int) -> dict[str, Any]:
        """Root of the Qobuz tab: the configured DLNA bridge server."""
        server = self._servers.get(self._qobuz_udn or "")
        if server is None:
            return {
                "id": None,
                "title": "Qobuz",
                "subtitle": None,
                "kind": "directory",
                "image": None,
                "can_play": False,
                "children": [],
                "total": 0,
                "next_start": None,
                "message": "qobuz_not_configured",
                "servers": [srv.name for srv in self._servers.values()],
            }
        page = await self.async_library_browse(make_content_id(server.udn, "0"), start)
        # bridges like BubbleUPnP Server offer several services: open "Qobuz" directly
        if start == 0:
            qobuz = [
                c for c in page["children"] if c["container"] and "qobuz" in c["title"].lower()
            ]
            only = page["children"][0] if len(page["children"]) == 1 else None
            if len(qobuz) == 1:
                page = await self.async_library_browse(qobuz[0]["id"])
            elif only is not None and only["container"]:
                page = await self.async_library_browse(only["id"])
        page["title"] = "Qobuz"
        page["can_play"] = False
        page["root"] = True
        return page

    # ----------------------------------------------------------------- radio
    def _tunein_client(self) -> TuneInClient:
        if self._tunein is None:
            self._tunein = TuneInClient(
                async_get_clientsession(self.hass), self.hass.config.language or "de"
            )
        return self._tunein

    @staticmethod
    def _radio_id(kind: str, value: str, **params: str | None) -> str:
        content_id = f"{URI_SCHEME_RADIO}://{kind}/{quote(value, safe='')}"
        query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items() if v)
        return f"{content_id}?{query}" if query else content_id

    @staticmethod
    def _parse_radio_id(content_id: str) -> tuple[str, str, dict[str, str]]:
        rest = content_id[len(URI_SCHEME_RADIO) + 3 :]
        path, _, query = rest.partition("?")
        kind, _, value = path.partition("/")
        params = {}
        for part in query.split("&"):
            key, _, val = part.partition("=")
            if key:
                params[key] = unquote(val)
        return kind, unquote(value), params

    def _radio_item(self, entry: RadioEntry) -> dict[str, Any]:
        if entry.kind == "link":
            return {
                "id": self._radio_id("b", entry.query or ""),
                "title": entry.title,
                "subtitle": entry.subtitle,
                "kind": "directory",
                "container": True,
                "image": entry.image,
                "section": entry.section,
            }
        details = " · ".join(
            x
            for x in (
                f"{entry.bitrate} kbit/s" if entry.bitrate else None,
                (entry.formats or "").upper() or None,
            )
            if x
        )
        return {
            "id": self._radio_id("s", entry.guide_id or "", name=entry.title, logo=entry.image),
            "title": entry.title,
            "subtitle": entry.subtitle,
            "details": details or None,
            "kind": "radio",
            "container": False,
            "image": entry.image,
            "section": entry.section,
        }

    async def _async_radio_browse(self, content_id: str) -> dict[str, Any]:
        kind, value, _params = self._parse_radio_id(content_id)
        page: dict[str, Any] = {
            "id": content_id,
            "subtitle": None,
            "kind": "directory",
            "image": None,
            "can_play": False,
            "next_start": None,
        }
        try:
            radio_page = await self._tunein_client().browse(value if kind == "b" else None)
        except TuneInError as err:
            if kind == "b" and "c=presets" in value:
                # usually a wrong username (e.g. the login e-mail): explain instead of failing
                _LOGGER.warning("TuneIn favourites could not be loaded: %s", err)
                return {
                    **page,
                    "title": "Meine TuneIn-Favoriten",
                    "children": [],
                    "total": 0,
                    "message": "tunein_presets_failed",
                }
            raise HomeAssistantError(str(err)) from err
        children = [self._radio_item(e) for e in radio_page.entries]
        root = kind != "b"
        if root and self._tunein_username:
            children.insert(
                0,
                {
                    "id": self._radio_id(
                        "b", urlencode({"c": "presets", "username": self._tunein_username})
                    ),
                    "title": "Meine TuneIn-Favoriten",
                    "subtitle": self._tunein_username,
                    "kind": "playlist",
                    "container": True,
                    "image": None,
                },
            )
        title = radio_page.title
        if kind == "b" and "c=presets" in value:
            title = "Meine TuneIn-Favoriten"
            if not children:
                page["message"] = "tunein_no_presets"
        return {
            **page,
            "title": "Radio" if root else title,
            "children": children,
            "total": len(children),
            "root": root,
        }

    async def _async_play_radio(self, content_id: str, mode: str) -> int:
        """Play a TuneIn station on the radio source of the Linn."""
        kind, value, params = self._parse_radio_id(content_id)
        try:
            renderer = await self._async_renderer()
            if kind == "s":
                client = self._tunein_client()
                url = await client.resolve(value)
                name, logo = params.get("name"), params.get("logo")
                if not name:
                    described = await client.describe(value)
                    name = described.title if described else value
                    logo = logo or (described.image if described else None)
                metadata = radio_metadata(value, name, logo, url)
                if renderer.has_radio:
                    await renderer.play_radio(url, metadata)
                else:
                    await renderer.load_tracks([PlaylistTrack(url, metadata)], mode)
            else:
                raise ServiceValidationError(f"Not a station: {content_id}")
        except TuneInError as err:
            raise HomeAssistantError(str(err)) from err
        except CONNECT_ERRORS as err:
            self._renderer = None
            raise HomeAssistantError(f"Playing the station failed: {err}") from err
        self.async_schedule_update_ha_state(True)
        return 1

    # ------------------------------------------------------- saved playlists
    def saved_playlists(self) -> list[dict[str, Any]]:
        """Summary of the saved playlists (for the cards)."""
        if self._saved is None:
            return []
        return [
            {**item, "image": self._art_url(item.get("image")) or item.get("image")}
            for item in self._saved.summary()
        ]

    async def async_save_playlist(self, name: str) -> None:
        """Service: save the current Linn playlist under a name."""
        if self._saved is None:
            raise HomeAssistantError("Saving playlists is not available")
        name = name.strip()
        try:
            renderer = await self._async_renderer()
            await self._queue.refresh(renderer)
        except CONNECT_ERRORS as err:
            self._renderer = None
            raise HomeAssistantError(f"Linn renderer not reachable: {err}") from err
        entries = self._queue.ordered()
        if not entries:
            raise ServiceValidationError("The Linn playlist is empty")
        durations = [e.duration for e in entries if e.duration]
        await self._saved.async_save(
            name,
            [{"uri": e.uri, "metadata": e.metadata} for e in entries],
            sum(durations) if durations else None,
            next((e.art for e in entries if e.art), None),
        )

    async def async_load_playlist(
        self, name: str, mode: str = MODE_REPLACE, play: bool | None = None
    ) -> None:
        """Service: load a saved playlist into the Linn."""
        await self.async_load_saved(name, mode, play)

    async def async_load_saved(
        self, name: str, mode: str = MODE_REPLACE, play: bool | None = None
    ) -> int:
        """Load a saved playlist and return the number of tracks."""
        tracks = self._saved.tracks(name) if self._saved else None
        if not tracks:
            raise ServiceValidationError(f"No saved playlist named {name!r}")
        try:
            renderer = await self._async_renderer()
            count = await renderer.load_tracks(
                [PlaylistTrack(t["uri"], t["metadata"]) for t in tracks], mode, play
            )
        except CONNECT_ERRORS as err:
            self._renderer = None
            raise HomeAssistantError(f"Loading the playlist failed: {err}") from err
        self.async_schedule_update_ha_state(True)
        return count

    async def async_delete_playlist(self, name: str) -> None:
        """Service: delete a saved playlist."""
        if self._saved is None or not await self._saved.async_delete(name):
            raise ServiceValidationError(f"No saved playlist named {name!r}")

    # -------------------------------------------------------- radio favourites
    async def async_radio_favorites(self, limit: int) -> dict[str, Any]:
        """The first TuneIn favourites, cached for a few minutes."""
        if not self._tunein_username:
            return {"configured": False, "items": []}
        now = time.monotonic()
        if self._favorites_cache is None or now - self._favorites_cache[0] > FAVORITES_TTL:
            page = await self._async_radio_browse(
                self._radio_id(
                    "b", urlencode({"c": "presets", "username": self._tunein_username})
                )
            )
            items = [c for c in page["children"] if c["kind"] == "radio"]
            if page.get("message"):
                return {"configured": True, "items": [], "message": page["message"]}
            self._favorites_cache = (now, items)
        return {"configured": True, "items": self._favorites_cache[1][:limit]}
