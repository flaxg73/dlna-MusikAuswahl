"""DLNA Musikauswahl: browse DLNA music servers and fill the playlist of a Linn DSM."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    Unauthorized,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CARD_VERSION,
    CARDS,
    CONF_MAX_TRACKS,
    CONF_QOBUZ_SERVER,
    CONF_RENDERER_LOCATION,
    CONF_RENDERER_UDN,
    CONF_SERVERS,
    CONF_TUNEIN_USERNAME,
    DEFAULT_MAX_TRACKS,
    DOMAIN,
    FRONTEND_URL,
    LOAD_MODES,
    QOBUZ_AUTO,
    QOBUZ_NAME_HINTS,
    QOBUZ_NONE,
)
from .dms import MediaServer
from .openhome import OpenHomeError, OpenHomeRenderer
from .saved import SavedPlaylists
from .upnp import CONNECT_ERRORS, async_connect

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

FRONTEND_DIR = Path(__file__).parent / "frontend"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the websocket API and the Lovelace card."""
    websocket_api.async_register_command(hass, ws_playlist)
    websocket_api.async_register_command(hass, ws_browse)
    websocket_api.async_register_command(hass, ws_search)
    websocket_api.async_register_command(hass, ws_load)
    websocket_api.async_register_command(hass, ws_saved_playlists)
    websocket_api.async_register_command(hass, ws_radio_favorites)
    websocket_api.async_register_command(hass, ws_random)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and load it automatically in every dashboard."""
    if getattr(hass, "http", None) is None:
        return
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_URL, str(FRONTEND_DIR), False)]
        )
    except RuntimeError:  # already registered (integration reloaded)
        pass
    if "frontend" in hass.config.components:
        from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415

        for card in CARDS:
            add_extra_js_url(hass, f"{FRONTEND_URL}/{card}?v={CARD_VERSION}")


def _ws_entity(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    policy: str = POLICY_READ,
) -> Any | None:
    """Return the player entity of a websocket message (or send an error)."""
    entity_id = msg["entity_id"]
    if not connection.user.permissions.check_entity(entity_id, policy):
        raise Unauthorized(entity_id=entity_id)
    entity = hass.data.get(DOMAIN, {}).get(entity_id)
    if entity is None:
        connection.send_error(msg["id"], "not_found", f"{entity_id} is not a Linn player")
    return entity


async def _ws_run(connection: websocket_api.ActiveConnection, msg_id: int, coro) -> None:
    try:
        result = await coro
    except HomeAssistantError as err:
        connection.send_error(msg_id, "failed", str(err))
        return
    connection.send_result(msg_id, result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dlna_musikauswahl/browse",
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("media_content_id"): vol.Any(None, str),
        vol.Optional("start", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("source", default="server"): vol.In(("server", "qobuz", "radio")),
    }
)
@websocket_api.async_response
async def ws_browse(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Browse the media servers of a Linn player (library card)."""
    if entity := _ws_entity(hass, connection, msg):
        await _ws_run(
            connection,
            msg["id"],
            entity.async_library_browse(
                msg.get("media_content_id"), msg["start"], msg["source"]
            ),
        )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dlna_musikauswahl/search",
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("query"): vol.All(str, vol.Length(min=1)),
        vol.Optional("media_content_id"): vol.Any(None, str),
        vol.Optional("source", default="server"): vol.In(("server", "qobuz", "radio")),
    }
)
@websocket_api.async_response
async def ws_search(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Search the media servers of a Linn player (library card)."""
    if entity := _ws_entity(hass, connection, msg):
        await _ws_run(
            connection,
            msg["id"],
            entity.async_library_search(
                msg["query"], msg.get("media_content_id"), msg["source"]
            ),
        )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dlna_musikauswahl/load",
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("media_content_id"): str,
        vol.Optional("mode", default="replace"): vol.In(LOAD_MODES),
    }
)
@websocket_api.async_response
async def ws_load(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Load media into the playlist and report the number of tracks."""
    if entity := _ws_entity(hass, connection, msg, POLICY_CONTROL):

        async def _load() -> dict[str, int]:
            content_id = msg["media_content_id"]
            if content_id.startswith("saved:"):
                count = await entity.async_load_saved(content_id[len("saved:") :], msg["mode"])
            else:
                count = await entity.async_load(content_id, msg["mode"])
            return {"count": count}

        await _ws_run(connection, msg["id"], _load())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dlna_musikauswahl/random",
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("media_content_id"): vol.Any(None, str),
        vol.Optional("count", default=100): vol.All(int, vol.Range(min=1, max=1000)),
        vol.Optional("mode", default="replace"): vol.In(LOAD_MODES),
    }
)
@websocket_api.async_response
async def ws_random(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Create a random playlist from a server or folder."""
    if entity := _ws_entity(hass, connection, msg, POLICY_CONTROL):

        async def _random() -> dict[str, int]:
            count = await entity.async_random(
                msg.get("media_content_id"), msg["count"], msg["mode"]
            )
            return {"count": count}

        await _ws_run(connection, msg["id"], _random())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dlna_musikauswahl/saved_playlists",
        vol.Required("entity_id"): cv.entity_id,
    }
)
@websocket_api.async_response
async def ws_saved_playlists(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List the playlists saved for a Linn player."""
    if entity := _ws_entity(hass, connection, msg):
        connection.send_result(msg["id"], {"playlists": entity.saved_playlists()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dlna_musikauswahl/radio_favorites",
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("limit", default=12): vol.All(int, vol.Range(min=1, max=100)),
    }
)
@websocket_api.async_response
async def ws_radio_favorites(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """First TuneIn favourites (player card)."""
    if entity := _ws_entity(hass, connection, msg):
        await _ws_run(connection, msg["id"], entity.async_radio_favorites(msg["limit"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "dlna_musikauswahl/playlist",
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("refresh", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_playlist(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the Linn playlist of a media player."""
    entity_id = msg["entity_id"]
    if not connection.user.permissions.check_entity(entity_id, POLICY_READ):
        raise Unauthorized(entity_id=entity_id)
    entity = hass.data.get(DOMAIN, {}).get(entity_id)
    if entity is None:
        connection.send_error(msg["id"], "not_found", f"{entity_id} is not a Linn player")
        return
    try:
        payload = await entity.async_playlist_payload(msg["refresh"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "unavailable", str(err))
        return
    connection.send_result(msg["id"], payload)


@dataclass
class RuntimeData:
    """Objects shared by the platforms of a config entry."""

    renderer: OpenHomeRenderer
    servers: dict[str, MediaServer] = field(default_factory=dict)
    max_tracks: int = DEFAULT_MAX_TRACKS
    qobuz_udn: str | None = None
    tunein_username: str | None = None
    saved: SavedPlaylists | None = None


def qobuz_server_udn(option: str | None, servers: list[dict[str, str]]) -> str | None:
    """Server used for the Qobuz tab: chosen in the options or guessed by name."""
    if option == QOBUZ_NONE:
        return None
    if option and option != QOBUZ_AUTO:
        return option if any(s.get("udn") == option for s in servers) else None
    for server in servers:
        name = str(server.get("name", "")).lower()
        if any(hint in name for hint in QOBUZ_NAME_HINTS):
            return server.get("udn")
    return None


type MusikConfigEntry = ConfigEntry[RuntimeData]


def _servers_config(entry: ConfigEntry) -> list[dict[str, str]]:
    return list(entry.options.get(CONF_SERVERS, entry.data.get(CONF_SERVERS, [])))


async def async_connect_renderer(
    hass: HomeAssistant, entry: ConfigEntry
) -> OpenHomeRenderer:
    """Connect to the renderer of a config entry, updating its location."""
    device, location = await async_connect(
        hass, entry.data[CONF_RENDERER_LOCATION], entry.data[CONF_RENDERER_UDN]
    )
    if location != entry.data[CONF_RENDERER_LOCATION]:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_RENDERER_LOCATION: location}
        )
    return OpenHomeRenderer(device)


async def async_setup_entry(hass: HomeAssistant, entry: MusikConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    try:
        renderer = await async_connect_renderer(hass, entry)
    except OpenHomeError as err:
        raise ConfigEntryNotReady(str(err)) from err
    except CONNECT_ERRORS as err:
        raise ConfigEntryNotReady(
            f"Linn renderer {entry.title} is not reachable: {err}"
        ) from err

    servers = {
        cfg["udn"]: MediaServer(hass, cfg) for cfg in _servers_config(entry) if cfg.get("udn")
    }
    saved = SavedPlaylists(hass, entry.entry_id)
    await saved.async_load()
    entry.runtime_data = RuntimeData(
        saved=saved,
        renderer=renderer,
        servers=servers,
        max_tracks=int(entry.options.get(CONF_MAX_TRACKS, DEFAULT_MAX_TRACKS)),
        tunein_username=(entry.options.get(CONF_TUNEIN_USERNAME) or "").strip() or None,
        qobuz_udn=qobuz_server_udn(
            entry.options.get(CONF_QOBUZ_SERVER), _servers_config(entry)
        ),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the saved playlists when the integration is removed."""
    await SavedPlaylists(hass, entry.entry_id).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: MusikConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
