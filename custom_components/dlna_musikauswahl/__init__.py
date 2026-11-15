"""DLNA Musikauswahl: browse DLNA music servers and fill the playlist of a Linn DSM."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_READ
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
    CARD_URL,
    CARD_VERSION,
    CONF_MAX_TRACKS,
    CONF_RENDERER_LOCATION,
    CONF_RENDERER_UDN,
    CONF_SERVERS,
    DEFAULT_MAX_TRACKS,
    DOMAIN,
)
from .dms import MediaServer
from .openhome import OpenHomeError, OpenHomeRenderer
from .upnp import CONNECT_ERRORS, async_connect

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_FILE = Path(__file__).parent / "frontend" / "linn-playlist-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the websocket API and the Lovelace card."""
    websocket_api.async_register_command(hass, ws_playlist)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and load it automatically in every dashboard."""
    if getattr(hass, "http", None) is None:
        return
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(CARD_FILE), False)]
        )
    except RuntimeError:  # already registered (integration reloaded)
        pass
    if "frontend" in hass.config.components:
        from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415

        add_extra_js_url(hass, f"{CARD_URL}?v={CARD_VERSION}")


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
    entry.runtime_data = RuntimeData(
        renderer=renderer,
        servers=servers,
        max_tracks=int(entry.options.get(CONF_MAX_TRACKS, DEFAULT_MAX_TRACKS)),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MusikConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
