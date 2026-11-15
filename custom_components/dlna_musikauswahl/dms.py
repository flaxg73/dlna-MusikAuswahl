"""Access to DLNA media servers (ContentDirectory)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from async_upnp_client.profiles.dlna import DmsDevice
from didl_lite import didl_lite
from homeassistant.core import HomeAssistant

from .const import MAX_RECURSION_DEPTH, SERVER_LOCATION, SERVER_NAME, SERVER_UDN
from .didl import is_container, is_playable_audio_item, item_metadata, pick_audio_resource
from .openhome import PlaylistTrack
from .upnp import CONNECT_ERRORS, async_connect

_LOGGER = logging.getLogger(__name__)

# Properties requested from the server. "*" is the most compatible choice,
# Asset, MinimServer and Linn/Kazoo Server all handle it well.
METADATA_FILTER = "*"
COLLECT_PAGE_SIZE = 200


@dataclass(slots=True)
class BrowsePage:
    """A page of children of a container."""

    container: didl_lite.DidlObject | None
    children: list[didl_lite.DidlObject]
    total: int


class MediaServer:
    """A configured DLNA media server."""

    def __init__(self, hass: HomeAssistant, config: dict[str, str]) -> None:
        """Initialise from the stored config dict."""
        self.hass = hass
        self.udn: str = config[SERVER_UDN]
        self.name: str = config.get(SERVER_NAME) or self.udn
        self.location: str = config.get(SERVER_LOCATION, "")
        self._device: DmsDevice | None = None
        self._lock = asyncio.Lock()

    @property
    def host(self) -> str | None:
        """Host name / IP of the server."""
        return urlparse(self.location).hostname if self.location else None

    async def _async_device(self) -> DmsDevice:
        async with self._lock:
            if self._device is None:
                upnp_device, location = await async_connect(self.hass, self.location, self.udn)
                self.location = location
                self._device = DmsDevice(upnp_device, None)
                self.name = self._device.name or self.name
            return self._device

    async def _async_retry(self, func: Callable):
        """Run a call against the device, reconnecting once if it fails."""
        try:
            return await func(await self._async_device())
        except CONNECT_ERRORS as err:
            _LOGGER.debug("Request to %s failed (%s), reconnecting", self.name, err)
            self._device = None
            return await func(await self._async_device())

    def absolute_url(self, url: str | None) -> str | None:
        """Resolve a (possibly relative) URL from the server."""
        if not url:
            return None
        if self._device is None:
            return url
        return self._device.get_absolute_url(url)

    async def async_metadata(self, object_id: str) -> didl_lite.DidlObject:
        """Return the metadata of a single object."""
        return await self._async_retry(
            lambda dev: dev.async_browse_metadata(object_id, metadata_filter=METADATA_FILTER)
        )

    async def async_browse(self, object_id: str, start: int, count: int) -> BrowsePage:
        """Return one page of children of a container."""

        async def _browse(dev: DmsDevice) -> BrowsePage:
            result = await dev.async_browse_direct_children(
                object_id,
                metadata_filter=METADATA_FILTER,
                starting_index=start,
                requested_count=count,
            )
            children = [o for o in result.result if isinstance(o, didl_lite.DidlObject)]
            return BrowsePage(None, children, result.total_matches or len(children))

        return await self._async_retry(_browse)

    @property
    def can_search(self) -> bool:
        """Return True if the server supports ContentDirectory search."""
        return self._device is not None and self._device.has_search_directory

    async def async_search(
        self, container_id: str, query: str, limit: int
    ) -> list[didl_lite.DidlObject]:
        """Search the server (title, artist and album)."""
        escaped = query.replace("\\", "\\\\").replace('"', '\\"')

        async def _search(dev: DmsDevice) -> list[didl_lite.DidlObject]:
            if not dev.has_search_directory:
                return []
            caps = {c.lower() for c in dev.search_capabilities}
            fields = [
                f
                for f in ("dc:title", "upnp:artist", "upnp:album", "dc:creator")
                if not caps or "*" in caps or f.lower() in caps
            ] or ["dc:title"]
            criteria = " or ".join(f'{f} contains "{escaped}"' for f in fields)
            result = await dev.async_search_directory(
                container_id,
                f"({criteria})",
                metadata_filter=METADATA_FILTER,
                requested_count=limit,
            )
            return [o for o in result.result if isinstance(o, didl_lite.DidlObject)]

        return await self._async_retry(_search)

    async def async_collect_tracks(self, object_id: str, limit: int) -> list[PlaylistTrack]:
        """Collect playable tracks of an item or (recursively) a container."""
        obj = await self.async_metadata(object_id)
        tracks: list[PlaylistTrack] = []
        if not is_container(obj):
            track = self.to_track(obj)
            return [track] if track else []
        await self._async_collect(object_id, tracks, limit, 0)
        return tracks

    async def _async_collect(
        self, object_id: str, tracks: list[PlaylistTrack], limit: int, depth: int
    ) -> None:
        start = 0
        while len(tracks) < limit:
            page = await self.async_browse(object_id, start, COLLECT_PAGE_SIZE)
            for child in page.children:
                if len(tracks) >= limit:
                    return
                if is_container(child):
                    if depth < MAX_RECURSION_DEPTH:
                        await self._async_collect(child.id, tracks, limit, depth + 1)
                elif track := self.to_track(child):
                    tracks.append(track)
            start += len(page.children)
            if not page.children or start >= page.total:
                return

    def to_track(self, obj: didl_lite.DidlObject) -> PlaylistTrack | None:
        """Convert a DIDL item into a playlist track."""
        if not is_playable_audio_item(obj):
            return None
        res = pick_audio_resource(obj)
        assert res is not None and res.uri
        return PlaylistTrack(uri=self.absolute_url(res.uri) or res.uri, metadata=item_metadata(obj))

