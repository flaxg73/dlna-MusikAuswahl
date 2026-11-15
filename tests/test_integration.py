"""End-to-end tests against fake UPnP devices."""

from __future__ import annotations

from unittest.mock import patch

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

from .fake_upnp import RENDERER_UDN, SERVER_UDN, FakeUpnp

ENTITY = "media_player.wohnzimmer"


async def _setup(hass: HomeAssistant, fake: FakeUpnp, options=None) -> MockConfigEntry:
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
            ],
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
    assert [c.title for c in root.children] == ["Alben", "Alle Titel"]

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
