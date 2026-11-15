"""DLNA Musikauswahl: browse DLNA music servers and fill the playlist of a Linn DSM."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_MAX_TRACKS,
    CONF_RENDERER_LOCATION,
    CONF_RENDERER_UDN,
    CONF_SERVERS,
    DEFAULT_MAX_TRACKS,
)
from .dms import MediaServer
from .openhome import OpenHomeError, OpenHomeRenderer
from .upnp import CONNECT_ERRORS, async_connect

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER]


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
