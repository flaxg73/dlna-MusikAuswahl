"""Fixtures for the DLNA Musikauswahl tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant

from .fake_upnp import FakeUpnp, Library, qobuz_library


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom_components."""
    return


@pytest.fixture
async def fake(hass: HomeAssistant, socket_enabled) -> AsyncGenerator[FakeUpnp]:
    """Start the fake Linn renderer + media server on localhost."""
    fake = FakeUpnp()
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    fake.base = str(server.make_url("")).rstrip("/")
    fake.library = Library(fake.base)
    fake.qobuz = qobuz_library(fake.base)
    # pretend the SSDP integration is running, without touching the network
    hass.config.components.add("ssdp")
    with (
        patch(
            "homeassistant.components.ssdp.async_get_discovery_info_by_udn",
            return_value=[],
        ),
        patch(
            "homeassistant.components.ssdp.async_get_discovery_info_by_st",
            return_value=[],
        ),
        patch("custom_components.dlna_musikauswahl.tunein.BASE_URL", f"{fake.base}/opml"),
    ):
        yield fake
    await server.close()
