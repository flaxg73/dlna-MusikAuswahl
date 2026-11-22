"""Access to DLNA media servers (ContentDirectory)."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from async_upnp_client.exceptions import (
    UpnpActionError,
    UpnpActionResponseError,
    UpnpError,
)
from async_upnp_client.profiles.dlna import DmsDevice
from didl_lite import didl_lite
from homeassistant.core import HomeAssistant

from .const import (
    MAX_RECURSION_DEPTH,
    SERVER_LOCATION,
    SERVER_NAME,
    SERVER_UDN,
    ST_MEDIA_SERVERS,
)
from .didl import (
    album_art_uri,
    describe,
    is_container,
    is_playable_audio_item,
    item_metadata,
    pick_audio_resource,
)
from .openhome import PlaylistTrack
from .upnp import CONNECT_ERRORS, async_connect, async_find_by_name

_LOGGER = logging.getLogger(__name__)

# Properties requested from the server. "*" is the most compatible choice,
# Asset, MinimServer and Linn/Kazoo Server all handle it well.
METADATA_FILTER = "*"
COLLECT_PAGE_SIZE = 200
RANDOM_SEARCH = 'upnp:class derivedfrom "object.item.audioItem"'
RANDOM_MAX_CONTAINERS = 150  # folders browsed at most when walking the tree
RANDOM_MAX_POOL = 5000  # tracks kept for sampling while walking
RANDOM_TIME_LIMIT = 25  # seconds
TITLE_CACHE_SIZE = 5000
FILTER_SCAN_LIMIT = 5000


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
        self._search_caps_loaded = False
        # titles seen in listings: some servers (e.g. upmpdcli plugins) answer
        # BrowseMetadata without a usable title, the listing has the real one
        self._titles: dict[str, str] = {}
        # cover, artist and class seen in listings (BrowseMetadata may lack them)
        self._details: dict[str, tuple[str | None, str | None, str]] = {}
        self._lock = asyncio.Lock()

    @property
    def host(self) -> str | None:
        """Host name / IP of the server."""
        return urlparse(self.location).hostname if self.location else None

    async def _async_device(self) -> DmsDevice:
        async with self._lock:
            if self._device is None:
                try:
                    upnp_device, location = await async_connect(
                        self.hass, self.location, self.udn
                    )
                except UpnpError:
                    found = await async_find_by_name(
                        self.hass, self.name, self.location, ST_MEDIA_SERVERS
                    )
                    if found is None:
                        raise
                    upnp_device, location = found
                    _LOGGER.warning(
                        "Media server %s now answers with device id %s instead of %s "
                        "(e.g. after recreating its container); using it. Select the new "
                        "entry in the integration options to make this permanent",
                        self.name,
                        upnp_device.udn,
                        self.udn,
                    )
                self.location = location
                self._device = DmsDevice(upnp_device, None)
                self.name = self._device.name or self.name
            return self._device

    async def _async_retry(self, func: Callable):
        """Run a call against the device, reconnecting once if it fails."""
        try:
            return await func(await self._async_device())
        except (UpnpActionError, UpnpActionResponseError):
            # the server answered, just not with success: no reason to reconnect
            raise
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
            self._remember_titles(children)
            return BrowsePage(None, children, result.total_matches or len(children))

        return await self._async_retry(_browse)

    def _remember_titles(self, objects: list[didl_lite.DidlObject]) -> None:
        for obj in objects:
            if not obj.id:
                continue
            title = getattr(obj, "title", None)
            if title and title != obj.id:
                self._titles[obj.id] = str(title)
            artist = getattr(obj, "artist", None) or getattr(obj, "creator", None)
            self._details[obj.id] = (
                self.absolute_url(album_art_uri(obj)),
                str(artist) if artist else None,
                str(getattr(obj, "upnp_class", "") or ""),
            )
        for cache in (self._titles, self._details):
            while len(cache) > TITLE_CACHE_SIZE:
                cache.pop(next(iter(cache)))

    def details_for(self, object_id: str) -> tuple[str | None, str | None, str]:
        """Cover URL, artist and UPnP class of an object as seen in a listing."""
        return self._details.get(object_id, (None, None, ""))

    def title_for(self, object_id: str, meta_title: str | None = None) -> str | None:
        """Readable title of an object (never its raw object id, if avoidable)."""
        if object_id in self._titles:
            return self._titles[object_id]
        if meta_title and meta_title != object_id:
            return meta_title
        return None

    @property
    def can_search(self) -> bool:
        """Return True if the server supports ContentDirectory search."""
        return self._device is not None and self._device.has_search_directory

    async def async_search(
        self, container_id: str, query: str, limit: int
    ) -> list[didl_lite.DidlObject]:
        """Search the server for title, artist and album.

        DLNA servers differ a lot here, so several variants are tried:
        the requested container first, then the root ("0") - many servers only
        search from the root - and several spellings of the search criteria.
        If the server cannot search at all, the children of the container are
        filtered instead.
        """
        escaped = query.replace("\\", "\\\\").replace('"', '\\"')

        async def _search(dev: DmsDevice) -> list[didl_lite.DidlObject]:
            if not dev.has_search_directory:
                return []
            if not self._search_caps_loaded:
                try:
                    await dev.async_update()
                except CONNECT_ERRORS as err:
                    _LOGGER.debug("GetSearchCapabilities on %s failed: %s", self.name, err)
                self._search_caps_loaded = True
            caps = {c.strip().lower() for c in dev.search_capabilities}
            fields = [
                f
                for f in ("dc:title", "upnp:artist", "upnp:album", "dc:creator")
                if not caps or "*" in caps or f.lower() in caps
            ] or ["dc:title"]
            any_field = " or ".join(f'{f} contains "{escaped}"' for f in fields)
            title = f'dc:title contains "{escaped}"'
            variants = list(
                dict.fromkeys(
                    [
                        any_field,
                        f"({any_field})",
                        title,
                        f'upnp:class derivedfrom "object.item.audioItem" and {title}',
                    ]
                )
            )
            containers = list(dict.fromkeys([container_id, "0"]))
            for cid in containers:
                for criteria in variants:
                    try:
                        result = await dev.async_search_directory(
                            cid,
                            criteria,
                            metadata_filter=METADATA_FILTER,
                            requested_count=limit,
                        )
                    except (UpnpActionError, UpnpActionResponseError) as err:
                        _LOGGER.debug(
                            "Search %r in %r on %s rejected: %s", criteria, cid, self.name, err
                        )
                        continue
                    found = [o for o in result.result if isinstance(o, didl_lite.DidlObject)]
                    _LOGGER.debug(
                        "Search %r in %r on %s: %s results", criteria, cid, self.name, len(found)
                    )
                    if found:
                        self._remember_titles(found)
                        return found
            return []

        found = await self._async_retry(_search)
        if found:
            return found
        return await self._async_filter_children(container_id, query, limit)

    async def _async_filter_children(
        self, container_id: str, query: str, limit: int
    ) -> list[didl_lite.DidlObject]:
        """Fallback search: match the entries of a container by name."""
        needle = query.casefold()
        found: list[didl_lite.DidlObject] = []
        start = 0
        while start < FILTER_SCAN_LIMIT and len(found) < limit:
            page = await self.async_browse(container_id, start, COLLECT_PAGE_SIZE)
            for child in page.children:
                text = " ".join(
                    str(getattr(child, attr, None) or "")
                    for attr in ("title", "artist", "album", "creator")
                ).casefold()
                if needle in text:
                    found.append(child)
            start += len(page.children)
            if not page.children or start >= page.total:
                break
        _LOGGER.debug(
            "Filtered %s entries of %r on %s for %r: %s matches",
            start, container_id, self.name, query, len(found),
        )
        return found[:limit]

    async def async_collect_tracks(
        self,
        object_id: str,
        limit: int,
        parent_id: str | None = None,
        is_item: bool | None = None,
    ) -> list[PlaylistTrack]:
        """Collect playable tracks of an item or (recursively) a container.

        BrowseMetadata is not used to decide what an object is: several servers
        answer it incompletely (no <res>, wrong class). Containers are detected
        by browsing their children, items are taken from the listing of their
        parent, exactly as they were shown in the media browser.
        """
        tracks: list[PlaylistTrack] = []

        # 1. container? (skipped when the media browser told us it is a track)
        if not is_item:
            try:
                await self._async_collect(object_id, tracks, limit, 0)
            except CONNECT_ERRORS as err:
                _LOGGER.debug("BrowseDirectChildren of %s failed: %s", object_id, err)
            if tracks:
                return tracks

        # 2. item: metadata of the object itself
        obj: didl_lite.DidlObject | None = None
        try:
            obj = await self.async_metadata(object_id)
        except (*CONNECT_ERRORS, AssertionError, IndexError) as err:
            _LOGGER.debug("BrowseMetadata of %s failed: %s", object_id, err)
        if obj is not None:
            if track := self.to_track(obj):
                return [track]
            parent_id = parent_id or getattr(obj, "parent_id", None)

        # 3. item: look it up in the listing of its parent container
        if parent_id and parent_id != "-1":
            if track := await self._async_find_in_parent(parent_id, object_id):
                return [track]

        _LOGGER.warning(
            "No playable audio found for object %r on %s (parent %r). Metadata: %s",
            object_id,
            self.name,
            parent_id,
            describe(obj) if obj is not None else "-",
        )
        return []

    async def _async_find_in_parent(
        self, parent_id: str, object_id: str
    ) -> PlaylistTrack | None:
        start = 0
        while True:
            try:
                page = await self.async_browse(parent_id, start, COLLECT_PAGE_SIZE)
            except CONNECT_ERRORS as err:
                _LOGGER.debug("Browsing parent %s failed: %s", parent_id, err)
                return None
            for child in page.children:
                if child.id == object_id:
                    track = self.to_track(child)
                    if track is None:
                        _LOGGER.debug("Object in parent listing: %s", describe(child))
                    return track
            start += len(page.children)
            if not page.children or start >= page.total:
                return None

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

    # ------------------------------------------------------------ random
    async def async_random_tracks(self, container_id: str, count: int) -> list[PlaylistTrack]:
        """Pick random tracks below a container (whole library, a genre, an artist …).

        Servers that can search (MinimServer, Asset …) deliver all audio items
        below the container, so random positions of that result are fetched.
        Otherwise the folder tree is walked in random order (bounded by number
        of folders and time) and the tracks found are sampled.
        """
        tracks = await self._async_random_by_search(container_id, count)
        if len(tracks) < count:
            seen = {t.uri for t in tracks}
            walked = await self._async_random_by_walking(container_id, count)
            tracks += [t for t in walked if t.uri not in seen][: count - len(tracks)]
        random.shuffle(tracks)
        return tracks[:count]

    async def _async_search_page(
        self, container_id: str, start: int, count: int
    ) -> tuple[list[didl_lite.DidlObject], int] | None:
        async def _search(dev: DmsDevice):
            if not dev.has_search_directory:
                return None
            result = await dev.async_search_directory(
                container_id,
                RANDOM_SEARCH,
                metadata_filter=METADATA_FILTER,
                starting_index=start,
                requested_count=count,
            )
            objects = [o for o in result.result if isinstance(o, didl_lite.DidlObject)]
            return objects, result.total_matches

        try:
            return await self._async_retry(_search)
        except (UpnpActionError, UpnpActionResponseError) as err:
            _LOGGER.debug("Search for random tracks in %r rejected: %s", container_id, err)
            return None

    async def _async_random_by_search(self, container_id: str, count: int) -> list[PlaylistTrack]:
        first = await self._async_search_page(container_id, 0, 1)
        if first is None:
            return []
        objects, total = first
        if not total:
            # some servers do not report a total: take what one big page gives
            page = await self._async_search_page(container_id, 0, RANDOM_MAX_POOL)
            if page is None:
                return []
            pool = [t for o in page[0] if (t := self.to_track(o))]
            return random.sample(pool, min(count, len(pool)))
        if total <= count * 3:
            # small selection: read it completely and shuffle
            pool: list[PlaylistTrack] = []
            for start in range(0, total, COLLECT_PAGE_SIZE):
                page = await self._async_search_page(container_id, start, COLLECT_PAGE_SIZE)
                if page is None:
                    break
                pool += [t for o in page[0] if (t := self.to_track(o))]
            return random.sample(pool, min(count, len(pool)))
        # large selection: fetch single items at random positions
        positions = random.sample(range(total), min(total, count + count // 3))
        semaphore = asyncio.Semaphore(4)

        async def _one(position: int) -> PlaylistTrack | None:
            async with semaphore:
                page = await self._async_search_page(container_id, position, 1)
            if not page or not page[0]:
                return None
            return self.to_track(page[0][0])

        found = await asyncio.gather(*(_one(p) for p in positions), return_exceptions=True)
        tracks: list[PlaylistTrack] = []
        seen: set[str] = set()
        for track in found:
            if isinstance(track, PlaylistTrack) and track.uri not in seen:
                seen.add(track.uri)
                tracks.append(track)
        _LOGGER.debug(
            "Random tracks via search in %r on %s: %s of %s", container_id, self.name,
            len(tracks), total,
        )
        return tracks[:count]

    async def _async_random_by_walking(self, container_id: str, count: int) -> list[PlaylistTrack]:
        deadline = time.monotonic() + RANDOM_TIME_LIMIT
        frontier = [container_id]
        pool: list[PlaylistTrack] = []
        seen_tracks = 0
        browsed = 0
        while frontier and browsed < RANDOM_MAX_CONTAINERS and time.monotonic() < deadline:
            current = frontier.pop(random.randrange(len(frontier)))
            browsed += 1
            try:
                first = await self.async_browse(current, 0, COLLECT_PAGE_SIZE)
            except CONNECT_ERRORS as err:
                _LOGGER.debug("Browsing %r for random tracks failed: %s", current, err)
                continue
            pages = [first]
            if first.total > COLLECT_PAGE_SIZE:
                # big folder: one more page at a random position
                start = random.randrange(COLLECT_PAGE_SIZE, first.total)
                try:
                    pages.append(await self.async_browse(current, start, COLLECT_PAGE_SIZE))
                except CONNECT_ERRORS:
                    pass
            for page in pages:
                for child in page.children:
                    if is_container(child):
                        frontier.append(child.id)
                    elif track := self.to_track(child):
                        # reservoir sampling keeps a uniform sample of everything seen
                        seen_tracks += 1
                        if len(pool) < RANDOM_MAX_POOL:
                            pool.append(track)
                        else:
                            slot = random.randrange(seen_tracks)
                            if slot < RANDOM_MAX_POOL:
                                pool[slot] = track
            # enough variety collected
            if len(pool) >= count * 8 and browsed >= 10:
                break
        _LOGGER.debug(
            "Random tracks by walking %r on %s: %s folders, %s tracks seen",
            container_id, self.name, browsed, seen_tracks,
        )
        unique = list({t.uri: t for t in pool}.values())
        return random.sample(unique, min(count, len(unique)))

    def to_track(self, obj: didl_lite.DidlObject) -> PlaylistTrack | None:
        """Convert a DIDL item into a playlist track."""
        if not is_playable_audio_item(obj):
            return None
        res = pick_audio_resource(obj)
        assert res is not None and res.uri
        return PlaylistTrack(uri=self.absolute_url(res.uri) or res.uri, metadata=item_metadata(obj))

