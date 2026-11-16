"""End-to-end tests against fake UPnP devices."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_ENQUEUE,
    ATTR_MEDIA_VOLUME_LEVEL,
    SERVICE_CLEAR_PLAYLIST,
    SERVICE_PLAY_MEDIA,
    SERVICE_VOLUME_SET,
    MediaPlayerEnqueue,
    SearchMediaQuery,
)
from homeassistant.components.media_player import (
    DOMAIN as MP_DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dlna_musikauswahl.const import (
    CONF_MAX_TRACKS,
    CONF_RENDERER_LOCATION,
    CONF_RENDERER_NAME,
    CONF_RENDERER_UDN,
    CONF_SERVERS,
    DOMAIN,
)
from custom_components.dlna_musikauswahl.media_player import make_content_id

from .fake_upnp import QOBUZ_UDN, RENDERER_UDN, SERVER_UDN, FakeUpnp

ENTITY = "media_player.wohnzimmer"


async def _setup(
    hass: HomeAssistant, fake: FakeUpnp, options=None, qobuz: bool = False
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wohnzimmer",
        unique_id=RENDERER_UDN,
        data={
            CONF_RENDERER_LOCATION: f"{fake.base}/renderer.xml",
            CONF_RENDERER_UDN: RENDERER_UDN,
            CONF_RENDERER_NAME: "Wohnzimmer",
            CONF_SERVERS: [
                {"udn": SERVER_UDN, "name": "MinimServer", "location": f"{fake.base}/server.xml"}
            ] + ([{"udn": QOBUZ_UDN, "name": "UpMpd-mediaserver",
                   "location": f"{fake.base}/qobuz.xml"}] if qobuz else []),
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity(hass: HomeAssistant):
    component = hass.data["entity_components"][MP_DOMAIN]
    return component.get_entity(ENTITY)


def _cid(object_id: str, start: int = 0) -> str:
    return make_content_id(SERVER_UDN, object_id, start)


async def test_config_flow_manual(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """User enters renderer URL and server URL manually."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": f"{fake.base}/server.xml"}
    )
    assert result["errors"] == {"base": "not_openhome"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": f"{fake.base}/renderer.xml"}
    )
    assert result["step_id"] == "servers"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"manual_servers": ""}
    )
    assert result["errors"] == {"base": "no_servers"}

    with patch(
        "custom_components.dlna_musikauswahl.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"manual_servers": f"{fake.base}/server.xml"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wohnzimmer"
    assert result["data"][CONF_RENDERER_UDN] == RENDERER_UDN
    assert result["data"][CONF_SERVERS] == [
        {"udn": SERVER_UDN, "name": "MinimServer", "location": f"{fake.base}/server.xml"}
    ]


async def test_config_flow_discovered(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """Renderer and server are offered from the SSDP cache."""
    renderer_info = SsdpServiceInfo(
        ssdp_usn=f"{RENDERER_UDN}::x", ssdp_st="x", ssdp_udn=RENDERER_UDN,
        ssdp_location=f"{fake.base}/renderer.xml", upnp={"friendlyName": "Wohnzimmer"},
    )
    server_info = SsdpServiceInfo(
        ssdp_usn=f"{SERVER_UDN}::x", ssdp_st="x", ssdp_udn=SERVER_UDN,
        ssdp_location=f"{fake.base}/server.xml", upnp={"friendlyName": "MinimServer"},
    )

    async def by_st(hass, st):
        if "Playlist" in st:
            return [renderer_info]
        if st.endswith("MediaServer:1"):
            return [server_info]
        return []

    with patch("homeassistant.components.ssdp.async_get_discovery_info_by_st", by_st):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"renderer": f"{fake.base}/renderer.xml"}
        )
        assert result["step_id"] == "servers"
        with patch(
            "custom_components.dlna_musikauswahl.async_setup_entry", return_value=True
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"server_selection": [SERVER_UDN]}
            )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERVERS][0]["udn"] == SERVER_UDN


async def test_browse(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """Browse the library, including paging and filtering of non-audio items."""
    await _setup(hass, fake)
    entity = _entity(hass)

    root = await entity.async_browse_media()
    assert [c.title for c in root.children] == [
        "Aktuelle Linn-Playlist (0 Titel)", "MinimServer"]
    server_root = await entity.async_browse_media("playlist", root.children[1].media_content_id)
    assert [c.title for c in server_root.children] == ["Alben", "Alle Titel"]

    albums = await entity.async_browse_media("playlist", _cid("albums"))
    assert [c.title for c in albums.children] == ["Album 1", "Album 2"]
    assert albums.children[0].media_class == "album"
    assert albums.children[0].thumbnail.startswith(f"/api/media_player_proxy/{ENTITY}")

    album = await entity.async_browse_media("playlist", _cid("album1"))
    assert [c.title for c in album.children] == ["Track 1.1", "Track 1.2", "Track 1.3"]
    assert album.can_play and all(c.can_play and not c.can_expand for c in album.children)

    big = await entity.async_browse_media("playlist", _cid("big"))
    assert len(big.children) == 251
    assert big.children[-1].title.startswith("Weitere")
    page = await entity.async_browse_media("playlist", big.children[-1].media_content_id)
    assert page.children[0].title == "Song 250"

    # cover art is proxied only from the media server host
    image, mime = await entity.async_get_browse_image(
        "music", _cid("album1"), f"{fake.base}/art/album1.jpg"
    )
    assert mime == "image/jpeg" and image.startswith(b"\xff\xd8")
    assert await entity.async_get_browse_image(
        "music", _cid("album1"), "http://evil.example/x.jpg"
    ) == (None, None)


async def test_play_and_enqueue(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """Load albums/tracks into the Linn playlist in all enqueue modes."""
    await _setup(hass, fake)
    assert hass.states.get(ENTITY).state == "off"

    async def play(object_id: str, enqueue=None):
        data = {
            ATTR_ENTITY_ID: ENTITY,
            ATTR_MEDIA_CONTENT_TYPE: "music",
            ATTR_MEDIA_CONTENT_ID: _cid(object_id),
        }
        if enqueue:
            data[ATTR_MEDIA_ENQUEUE] = enqueue
        await hass.services.async_call(MP_DOMAIN, SERVICE_PLAY_MEDIA, data, blocking=True)
        await hass.async_block_till_done()

    await play("album1")
    assert not fake.standby and fake.source_index == 0
    assert [u.rsplit("/", 1)[1] for _, u, _ in fake.playlist] == [
        "album1-t1.flac", "album1-t2.flac", "album1-t3.flac"]
    assert fake.transport == "Playing" and fake.current_id == fake.playlist[0][0]
    metadata = fake.playlist[0][2]
    assert 'duration="0:04:05.000"' in metadata and 'bitsPerSample="24"' in metadata
    assert "<upnp:album>Album 1</upnp:album>" in metadata and "pv:" not in metadata

    state = hass.states.get(ENTITY)
    assert state.state == "playing"
    assert state.attributes["media_title"] == "Track 1.1"
    assert state.attributes["media_artist"] == "The Band"
    assert state.attributes["media_album_name"] == "Album 1"
    assert state.attributes["media_duration"] == 245

    await play("album2", MediaPlayerEnqueue.ADD)
    assert len(fake.playlist) == 6 and fake.playlist[-1][1].endswith("album2-t3.flac")

    await play("album2-t1", MediaPlayerEnqueue.NEXT)
    assert fake.playlist[1][1].endswith("album2-t1.flac")
    assert fake.current_id == fake.playlist[0][0]

    await play("album2-t2", MediaPlayerEnqueue.PLAY)
    assert fake.playlist[1][1].endswith("album2-t2.flac")
    assert fake.current_id == fake.playlist[1][0]

    await play("album1", MediaPlayerEnqueue.REPLACE)
    assert len(fake.playlist) == 3

    await hass.services.async_call(
        MP_DOMAIN, SERVICE_CLEAR_PLAYLIST, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )
    assert fake.playlist == []


async def test_load_media_service_limit(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """The custom service honours mode and the configured track limit."""
    await _setup(hass, fake, options={CONF_MAX_TRACKS: 100})
    await hass.services.async_call(
        DOMAIN,
        "load_media",
        {ATTR_ENTITY_ID: ENTITY, "media_content_id": _cid("big"), "mode": "add"},
        blocking=True,
    )
    assert len(fake.playlist) == 100
    assert fake.transport == "Stopped"

    # whole folder tree: Alben -> 2 albums -> 6 tracks (cover images skipped)
    await hass.services.async_call(
        DOMAIN,
        "load_media",
        {ATTR_ENTITY_ID: ENTITY, "media_content_id": _cid("albums"), "mode": "replace"},
        blocking=True,
    )
    assert len(fake.playlist) == 6 and fake.transport == "Playing"


async def test_search_and_controls(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """Search and the basic player controls."""
    await _setup(hass, fake)
    entity = _entity(hass)
    result = await entity.async_search_media(SearchMediaQuery(search_query="Track 2."))
    assert sorted(r.title for r in result.result) == ["Track 2.1", "Track 2.2", "Track 2.3"]

    fake.standby = False
    await hass.services.async_call(
        MP_DOMAIN, SERVICE_VOLUME_SET,
        {ATTR_ENTITY_ID: ENTITY, ATTR_MEDIA_VOLUME_LEVEL: 0.42}, blocking=True,
    )
    assert fake.volume == 42
    await hass.services.async_call(
        MP_DOMAIN, "select_source", {ATTR_ENTITY_ID: ENTITY, "source": "Radio"}, blocking=True
    )
    assert fake.source_index == 1
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY)
    assert state.attributes["source"] == "Radio"
    assert state.attributes["source_list"] == ["Playlist", "Radio", "TV"]


async def test_unavailable_renderer(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """Setup is retried when the renderer cannot be reached."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Wohnzimmer", unique_id=RENDERER_UDN,
        data={
            CONF_RENDERER_LOCATION: "http://127.0.0.1:1/renderer.xml",
            CONF_RENDERER_UDN: RENDERER_UDN, CONF_RENDERER_NAME: "Wohnzimmer",
            CONF_SERVERS: [],
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state.value == "setup_retry"


async def test_ssdp_discovery(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """A renderer announced via SSDP leads to a confirm form."""
    info = SsdpServiceInfo(
        ssdp_usn=f"{RENDERER_UDN}::x", ssdp_st="urn:av-openhome-org:service:Playlist:1",
        ssdp_udn=RENDERER_UDN, ssdp_location=f"{fake.base}/renderer.xml", upnp={},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "ssdp"}, data=info
    )
    assert result["step_id"] == "confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "servers"


async def test_options_flow(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """Servers and the track limit can be changed later."""
    entry = await _setup(hass, fake)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"server_selection": [SERVER_UDN], CONF_MAX_TRACKS: 250}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_MAX_TRACKS] == 250
    assert entry.options[CONF_SERVERS][0]["udn"] == SERVER_UDN
    assert entry.runtime_data.max_tracks == 250


@pytest.mark.parametrize("quirk", ["no_res", "error"])
async def test_broken_browse_metadata(hass: HomeAssistant, fake: FakeUpnp, quirk) -> None:
    """Albums and tracks load although the server answers BrowseMetadata badly."""
    await _setup(hass, fake)
    fake.metadata_quirk = quirk
    entity = _entity(hass)
    albums = await entity.async_browse_media("playlist", _cid("albums"))
    album1 = albums.children[0]
    album = await entity.async_browse_media(album1.media_content_type, album1.media_content_id)
    track = album.children[1]
    assert "?parent=album1" in track.media_content_id

    async def play(item, enqueue=MediaPlayerEnqueue.REPLACE):
        await hass.services.async_call(
            MP_DOMAIN, SERVICE_PLAY_MEDIA,
            {ATTR_ENTITY_ID: ENTITY, ATTR_MEDIA_CONTENT_TYPE: item.media_content_type,
             ATTR_MEDIA_CONTENT_ID: item.media_content_id, ATTR_MEDIA_ENQUEUE: enqueue},
            blocking=True,
        )

    await play(album1)
    assert len(fake.playlist) == 3
    await play(track, MediaPlayerEnqueue.ADD)
    assert len(fake.playlist) == 4 and fake.playlist[-1][1].endswith("album1-t2.flac")
    # also via the service without type hint and without parent in the id
    await hass.services.async_call(
        DOMAIN, "load_media",
        {ATTR_ENTITY_ID: ENTITY, "media_content_id": track.media_content_id, "mode": "add"},
        blocking=True,
    )
    assert len(fake.playlist) == 5


async def _load(hass: HomeAssistant, object_id: str, mode: str = "add") -> None:
    await hass.services.async_call(
        DOMAIN, "load_media",
        {ATTR_ENTITY_ID: ENTITY, "media_content_id": _cid(object_id), "mode": mode},
        blocking=True,
    )
    await hass.async_block_till_done()


def _order(fake: FakeUpnp) -> list[str]:
    return [u.rsplit("/", 1)[1].removesuffix(".flac") for _, u, _ in fake.playlist]


async def test_playlist_websocket_and_services(
    hass: HomeAssistant, fake: FakeUpnp, hass_ws_client
) -> None:
    """The card reads the playlist via websocket and edits it via services."""
    await _setup(hass, fake)
    await _load(hass, "album1", "replace")
    await _load(hass, "album2")
    assert _order(fake) == ["album1-t1", "album1-t2", "album1-t3",
                            "album2-t1", "album2-t2", "album2-t3"]
    state = hass.states.get(ENTITY)
    assert state.attributes["playlist_length"] == 6
    assert state.attributes["playlist_position"] == 1
    assert state.attributes["current_track_id"] == fake.playlist[0][0]

    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "dlna_musikauswahl/playlist", "entity_id": ENTITY, "refresh": True}
    )
    msg = await client.receive_json()
    assert msg["success"], msg
    tracks = msg["result"]["tracks"]
    assert [t["title"] for t in tracks][:2] == ["Track 1.1", "Track 1.2"]
    assert tracks[0]["artist"] == "The Band" and tracks[0]["album"] == "Album 1"
    assert tracks[0]["duration"] == 245
    assert tracks[0]["image"].startswith(f"/api/media_player_proxy/{ENTITY}/browse_media/cover/")
    assert msg["result"]["current_id"] == tracks[0]["id"]

    # proxied cover works for configured server hosts only
    entity = _entity(hass)
    assert (await entity.async_get_browse_image(
        "cover", "cover", f"{fake.base}/art/album1.jpg"))[1] == "image/jpeg"
    assert await entity.async_get_browse_image(
        "cover", "cover", "http://evil.example/a.jpg") == (None, None)

    async def svc(service, **data):
        await hass.services.async_call(
            DOMAIN, service, {ATTR_ENTITY_ID: ENTITY, **data}, blocking=True
        )
        await hass.async_block_till_done()

    ids = [i for i, _, _ in fake.playlist]
    await svc("move_track", track_id=ids[5], position=1)
    assert _order(fake)[0] == "album2-t3" and len(fake.playlist) == 6
    await svc("move_track", track_id=fake.playlist[1][0], position=99)  # album1-t1 to end
    assert _order(fake) == ["album2-t3", "album1-t2", "album1-t3",
                            "album2-t1", "album2-t2", "album1-t1"]
    # album1-t1 was playing: playback continues with the moved track
    assert fake.current_id == fake.playlist[-1][0] and fake.transport == "Playing"

    await svc("play_track", track_id=fake.playlist[2][0])
    assert fake.current_id == fake.playlist[2][0]

    await svc("remove_tracks", track_ids=[fake.playlist[0][0], fake.playlist[1][0]])
    assert _order(fake) == ["album1-t3", "album2-t1", "album2-t2", "album1-t1"]
    assert hass.states.get(ENTITY).attributes["playlist_length"] == 4

    with pytest.raises(Exception, match="not in the Linn playlist"):
        await svc("play_track", track_id=9999)

    await client.send_json({"id": 2, "type": "dlna_musikauswahl/playlist", "entity_id": ENTITY})
    msg = await client.receive_json()
    assert [t["title"] for t in msg["result"]["tracks"]] == [
        "Track 1.3", "Track 2.1", "Track 2.2", "Track 1.1"]

    await client.send_json(
        {"id": 3, "type": "dlna_musikauswahl/playlist", "entity_id": "media_player.nope"}
    )
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "not_found"


async def test_browse_and_play_queue(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """The Linn playlist is a folder in the media browser."""
    await _setup(hass, fake)
    await _load(hass, "album1", "replace")
    entity = _entity(hass)
    root = await entity.async_browse_media()
    assert root.children[0].title == "Aktuelle Linn-Playlist (3 Titel)"
    queue = await entity.async_browse_media("playlist", root.children[0].media_content_id)
    assert [c.title for c in queue.children] == [
        "▶ 1. Track 1.1 (The Band – Album 1)",
        "2. Track 1.2 (The Band – Album 1)",
        "3. Track 1.3 (The Band – Album 1)",
    ]
    await hass.services.async_call(
        MP_DOMAIN, SERVICE_PLAY_MEDIA,
        {ATTR_ENTITY_ID: ENTITY, ATTR_MEDIA_CONTENT_TYPE: "track",
         ATTR_MEDIA_CONTENT_ID: queue.children[2].media_content_id},
        blocking=True,
    )
    assert fake.current_id == fake.playlist[2][0] and len(fake.playlist) == 3


async def test_card_is_served(hass: HomeAssistant, fake: FakeUpnp, hass_client) -> None:
    """The Lovelace card is served by the integration."""
    await _setup(hass, fake)
    client = await hass_client()
    resp = await client.get("/dlna_musikauswahl/linn-playlist-card.js")
    assert resp.status == 200
    assert "customElements.define(\"linn-playlist-card\"" in await resp.text()
    resp = await client.get("/dlna_musikauswahl/linn-library-card.js")
    assert resp.status == 200
    assert "customElements.define(\"linn-library-card\"" in await resp.text()


async def test_library_websocket(hass: HomeAssistant, fake: FakeUpnp, hass_ws_client) -> None:
    """Browse, search and load through the websocket API of the library card."""
    await _setup(hass, fake)
    client = await hass_ws_client(hass)
    msg_id = 0

    async def ws(type_, **data):
        nonlocal msg_id
        msg_id += 1
        await client.send_json({"id": msg_id, "type": f"dlna_musikauswahl/{type_}",
                                "entity_id": ENTITY, **data})
        return await client.receive_json()

    root = (await ws("browse"))["result"]
    assert [c["title"] for c in root["children"]] == ["MinimServer"]
    assert root["children"][0]["kind"] == "server"

    server = (await ws("browse", media_content_id=root["children"][0]["id"]))["result"]
    assert [c["title"] for c in server["children"]] == ["Alben", "Alle Titel"]
    assert server["can_play"] is False

    albums = (await ws("browse", media_content_id=server["children"][0]["id"]))["result"]
    album = albums["children"][0]
    assert album["kind"] == "album" and album["container"] and album["count"] == 4
    assert album["image"].startswith(f"/api/media_player_proxy/{ENTITY}/browse_media/cover/")

    page = (await ws("browse", media_content_id=album["id"]))["result"]
    assert page["title"] == "Album 1" and page["can_play"] and page["kind"] == "album"
    assert page["image"]
    tracks = page["children"]
    assert [t["title"] for t in tracks] == ["Track 1.1", "Track 1.2", "Track 1.3"]
    assert tracks[0]["duration"] == 245 and tracks[0]["track"] == 1
    assert tracks[0]["subtitle"] == "The Band" and not tracks[0]["container"]

    big = (await ws("browse", media_content_id=server["children"][1]["id"]))["result"]
    assert len(big["children"]) == 100 and big["total"] == 600 and big["next_start"] == 100
    more = (await ws("browse", media_content_id=big["id"], start=500))["result"]
    assert more["children"][0]["title"] == "Song 500" and more["next_start"] is None

    found = (await ws("search", query="Album 2"))["result"]["children"]
    assert found[0]["container"] and found[0]["title"] == "Album 2"

    result = await ws("load", media_content_id=album["id"], mode="replace")
    assert result["success"] and result["result"] == {"count": 3}
    result = await ws("load", media_content_id=tracks[1]["id"], mode="next")
    assert result["result"] == {"count": 1}
    assert _order(fake) == ["album1-t1", "album1-t2", "album1-t2", "album1-t3"]

    result = await ws("browse", media_content_id="dlnamusik://uuid%3Anope/0")
    assert not result["success"]


@pytest.mark.parametrize(
    "quirks",
    [set(), {"root_only"}, {"strict"}, {"no_res"}, {"root_only", "strict", "no_res"},
     {"unsupported"}],
)
async def test_search_quirks(
    hass: HomeAssistant, fake: FakeUpnp, hass_ws_client, quirks
) -> None:
    """Search works with picky servers and loads the found tracks."""
    await _setup(hass, fake)
    fake.search_quirks = quirks
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "dlna_musikauswahl/search", "entity_id": ENTITY,
                            "query": "track 2.", "media_content_id": _cid("album2")})
    msg = await client.receive_json()
    assert msg["success"], msg
    found = msg["result"]["children"]
    assert sorted(f["title"] for f in found) == ["Track 2.1", "Track 2.2", "Track 2.3"]

    await client.send_json({"id": 2, "type": "dlna_musikauswahl/load", "entity_id": ENTITY,
                            "media_content_id": found[0]["id"], "mode": "add"})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"] == {"count": 1}, msg
    assert fake.playlist[-1][1].endswith(".flac")



async def _ws_client(hass, hass_ws_client):
    client = await hass_ws_client(hass)
    counter = {"id": 0}

    async def ws(type_, **data):
        counter["id"] += 1
        await client.send_json({"id": counter["id"], "type": f"dlna_musikauswahl/{type_}",
                                "entity_id": ENTITY, **data})
        return await client.receive_json()

    return ws


async def test_radio(hass: HomeAssistant, fake: FakeUpnp, hass_ws_client) -> None:
    """TuneIn browsing/search and playing stations on the radio source."""
    await _setup(hass, fake)
    await _load(hass, "album1", "replace")
    playlist_before = list(fake.playlist)
    ws = await _ws_client(hass, hass_ws_client)

    root = (await ws("browse", source="radio"))["result"]
    assert root["title"] == "Radio" and root["root"]
    assert [c["title"] for c in root["children"]] == [
        "Meine Sender (Linn)", "Lokales Radio", "Musik"]

    presets = (await ws("browse", media_content_id=root["children"][0]["id"]))["result"]
    assert [p["title"] for p in presets["children"]] == ["Bayern 3", "FM4"]
    assert presets["children"][0]["image"] == "https://logos.example/7.png"

    local = (await ws("browse", media_content_id=root["children"][1]["id"]))["result"]
    assert local["title"] == "Lokales Radio"
    station = local["children"][0]
    assert station["title"] == "Radio Eins" and station["kind"] == "radio"
    assert station["section"] == "Sender" and station["details"] == "128 kbit/s · MP3"
    assert station["image"] == "https://cdn-radiotime-logos.tunein.com/s1001q.png"
    assert any("c=local" in r for r in fake.tunein_requests)

    result = await ws("load", media_content_id=station["id"], mode="replace")
    assert result["success"] and result["result"] == {"count": 1}, result
    assert fake.radio_channel[0] == "http://streams.example/s1001.mp3"
    assert "<dc:title>Radio Eins</dc:title>" in fake.radio_channel[1]
    assert "s1001q.png" in fake.radio_channel[1]
    assert fake.source_index == 1 and fake.radio_transport == "Playing"
    assert fake.playlist == playlist_before  # playlist untouched

    # .pls playlist instead of a direct stream
    await ws("load", media_content_id=local["children"][1]["id"])
    assert fake.radio_channel[0] == "http://streams.example/jazz.aac"

    found = (await ws("search", query="jazz", source="radio"))["result"]["children"]
    assert [f["title"] for f in found] == ["Jazz FM"]

    await ws("load", media_content_id=presets["children"][1]["id"])
    assert fake.radio_id == 9 and fake.radio_channel[0] == "http://streams.example/fm4.mp3"

    # play_media with a station id works as well
    await hass.services.async_call(
        MP_DOMAIN, SERVICE_PLAY_MEDIA,
        {ATTR_ENTITY_ID: ENTITY, ATTR_MEDIA_CONTENT_TYPE: "music",
         ATTR_MEDIA_CONTENT_ID: station["id"]},
        blocking=True,
    )
    assert fake.radio_channel[0] == "http://streams.example/s1001.mp3"


async def test_qobuz_tab(hass: HomeAssistant, fake: FakeUpnp, hass_ws_client) -> None:
    """A DLNA bridge server is shown in the Qobuz tab, not under the music servers."""
    await _setup(hass, fake, qobuz=True)
    ws = await _ws_client(hass, hass_ws_client)

    servers = (await ws("browse"))["result"]
    assert [c["title"] for c in servers["children"]] == ["MinimServer"]

    qobuz = (await ws("browse", source="qobuz"))["result"]
    assert qobuz["title"] == "Qobuz" and qobuz["root"]
    # jumped into the "Qobuz" folder of the bridge
    assert [c["title"] for c in qobuz["children"]] == ["Meine Alben"]
    albums = (await ws("browse", media_content_id=qobuz["children"][0]["id"]))["result"]
    album = albums["children"][0]
    assert album["image"] == "https://static.qobuz.example/qa1.jpg"
    result = await ws("load", media_content_id=album["id"], mode="replace")
    assert result["result"] == {"count": 2}
    assert fake.playlist[0][1].endswith("qa1-t1.flac")

    found = (await ws("search", query="qobuz track", source="qobuz"))["result"]["children"]
    assert sorted(f["title"] for f in found) == ["Qobuz Track 1", "Qobuz Track 2"]
    found = (await ws("search", query="track 1.1"))["result"]["children"]
    assert [f["title"] for f in found] == ["Track 1.1"]


async def test_qobuz_not_configured(hass: HomeAssistant, fake: FakeUpnp, hass_ws_client) -> None:
    """Without a bridge the Qobuz tab explains how to set it up."""
    await _setup(hass, fake)
    ws = await _ws_client(hass, hass_ws_client)
    qobuz = (await ws("browse", source="qobuz"))["result"]
    assert qobuz["message"] == "qobuz_not_configured" and qobuz["children"] == []
    assert qobuz["servers"] == ["MinimServer"]


async def test_qobuz_option(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """The bridge can be chosen (or switched off) in the options."""
    from custom_components.dlna_musikauswahl import qobuz_server_udn

    servers = [{"udn": "a", "name": "MinimServer"}, {"udn": "b", "name": "BubbleUPnP Media Server"}]
    assert qobuz_server_udn(None, servers) == "b"
    assert qobuz_server_udn("auto", servers) == "b"
    assert qobuz_server_udn("none", servers) is None
    assert qobuz_server_udn("a", servers) == "a"
    assert qobuz_server_udn("x", servers) is None

    entry = await _setup(hass, fake, qobuz=True)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"server_selection": [SERVER_UDN, QOBUZ_UDN], CONF_MAX_TRACKS: 500,
         "qobuz_server": "none"},
    )
    await hass.async_block_till_done()
    assert entry.options["qobuz_server"] == "none"
    assert entry.runtime_data.qobuz_udn is None



async def test_qobuz_option_adds_server(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """Choosing a discovered server as Qobuz bridge adds it to the servers."""
    entry = await _setup(hass, fake)
    discovered = {QOBUZ_UDN: {"udn": QOBUZ_UDN, "name": "Qobuz-mediaserver",
                              "location": f"{fake.base}/qobuz.xml"}}
    with patch(
        "custom_components.dlna_musikauswahl.config_flow._async_discovered_servers",
        return_value=discovered,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"server_selection": [SERVER_UDN], CONF_MAX_TRACKS: 500, "qobuz_server": QOBUZ_UDN},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [s["udn"] for s in entry.options[CONF_SERVERS]] == [SERVER_UDN, QOBUZ_UDN]
    assert entry.runtime_data.qobuz_udn == QOBUZ_UDN



@pytest.mark.parametrize(("username", "expected"), [("flo", ["Radio Eins"]), ("nobody", [])])
async def test_tunein_favourites(
    hass: HomeAssistant, fake: FakeUpnp, hass_ws_client, username, expected
) -> None:
    """With a TuneIn username the favourites appear at the top of the radio tab."""
    await _setup(hass, fake, options={"tunein_username": f" {username} "})
    ws = await _ws_client(hass, hass_ws_client)
    root = (await ws("browse", source="radio"))["result"]
    favourites = root["children"][0]
    assert favourites["title"] == "Meine TuneIn-Favoriten"
    assert favourites["subtitle"] == username
    assert root["children"][1]["title"] == "Meine Sender (Linn)"
    page = (await ws("browse", media_content_id=favourites["id"]))["result"]
    assert page["title"] == "Meine TuneIn-Favoriten"
    assert [c["title"] for c in page["children"]] == expected
    assert any(f"username={username}" in r for r in fake.tunein_requests)
    if not expected:
        assert page["message"] == "tunein_no_presets"
    else:
        result = await ws("load", media_content_id=page["children"][0]["id"])
        assert result["success"] and fake.radio_channel[0].endswith("s1001.mp3")


async def test_tunein_username_option(hass: HomeAssistant, fake: FakeUpnp) -> None:
    """The username is set in the options."""
    entry = await _setup(hass, fake)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"server_selection": [SERVER_UDN], CONF_MAX_TRACKS: 500, "tunein_username": "flo"},
    )
    await hass.async_block_till_done()
    assert entry.options["tunein_username"] == "flo"
    assert entry.runtime_data.tunein_username == "flo"
