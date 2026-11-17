"""Create UPnP devices, re-discovering them via SSDP when their URL changed."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import aiohttp
from async_upnp_client.aiohttp import AiohttpSessionRequester
from async_upnp_client.client import UpnpDevice
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.exceptions import UpnpError
from homeassistant.components import ssdp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15

CONNECT_ERRORS = (UpnpError, aiohttp.ClientError, TimeoutError, OSError, ValueError)


def _factory(hass: HomeAssistant) -> UpnpFactory:
    session = async_get_clientsession(hass, verify_ssl=False)
    requester = AiohttpSessionRequester(session, with_sleep=True, timeout=REQUEST_TIMEOUT)
    return UpnpFactory(requester, non_strict=True)


async def async_create_device(hass: HomeAssistant, location: str) -> UpnpDevice:
    """Create a device from the URL of its description XML."""
    return await _factory(hass).async_create_device(location)


async def async_locations_for_udn(hass: HomeAssistant, udn: str) -> list[str]:
    """Return all currently known description URLs for a UDN."""
    locations: list[str] = []
    for info in await ssdp.async_get_discovery_info_by_udn(hass, udn):
        if info.ssdp_location and info.ssdp_location not in locations:
            locations.append(info.ssdp_location)
    return locations


async def async_find_by_name(
    hass: HomeAssistant, name: str, near: str | None, device_types: tuple[str, ...]
) -> tuple[UpnpDevice, str] | None:
    """Find a device announced via SSDP under the same friendly name.

    Used when a device got a new UDN, e.g. a recreated container: servers
    like upmpdcli derive their UDN from the MAC address of the container.
    Devices on the same host as before are preferred.
    """
    wanted = name.strip().lower()
    old_host = urlparse(near).hostname if near else None
    candidates: list[str] = []
    for device_type in device_types:
        for info in await ssdp.async_get_discovery_info_by_st(hass, device_type):
            friendly = str(info.upnp.get("friendlyName") or "").strip().lower()
            if friendly == wanted and info.ssdp_location and info.ssdp_location not in candidates:
                candidates.append(info.ssdp_location)
    candidates.sort(key=lambda loc: urlparse(loc).hostname != old_host)
    for location in candidates:
        try:
            return await async_create_device(hass, location), location
        except CONNECT_ERRORS as err:
            _LOGGER.debug("Cannot connect to %s: %s", location, err)
    return None


async def async_connect(
    hass: HomeAssistant, location: str, udn: str | None
) -> tuple[UpnpDevice, str]:
    """Connect to a device; fall back to SSDP lookup by UDN.

    Returns the device and the location that worked.
    """
    candidates = [location] if location else []
    if udn:
        candidates += [
            loc for loc in await async_locations_for_udn(hass, udn) if loc not in candidates
        ]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            device = await async_create_device(hass, candidate)
        except CONNECT_ERRORS as err:
            _LOGGER.debug("Cannot connect to %s: %s", candidate, err)
            last_error = err
            continue
        if udn and udn.lower() not in {d.udn.lower() for d in device.all_devices}:
            _LOGGER.debug("Device at %s has UDN %s, expected %s", candidate, device.udn, udn)
            last_error = UpnpError(f"unexpected device at {candidate}")
            continue
        return device, candidate
    raise UpnpError(f"Device {udn or location} not reachable") from last_error
