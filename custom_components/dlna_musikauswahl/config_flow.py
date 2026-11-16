"""Config flow for DLNA Musikauswahl."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import ssdp
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .const import (
    CONF_MAX_TRACKS,
    CONF_QOBUZ_SERVER,
    CONF_RENDERER_LOCATION,
    CONF_RENDERER_NAME,
    CONF_RENDERER_UDN,
    CONF_SERVERS,
    DEFAULT_MAX_TRACKS,
    DOMAIN,
    QOBUZ_AUTO,
    QOBUZ_NONE,
    SERVER_LOCATION,
    SERVER_NAME,
    SERVER_UDN,
    ST_MEDIA_SERVERS,
    ST_OPENHOME_PLAYLIST,
)
from .openhome import OpenHomeError, OpenHomeRenderer
from .upnp import CONNECT_ERRORS, async_create_device

_LOGGER = logging.getLogger(__name__)

MANUAL = "__manual__"
CONF_RENDERER = "renderer"
CONF_URL = "url"
CONF_SERVER_SELECTION = "server_selection"
CONF_MANUAL_SERVERS = "manual_servers"


def _friendly_name(info: SsdpServiceInfo) -> str:
    return str(info.upnp.get("friendlyName") or info.ssdp_location or info.ssdp_udn)


async def _async_discovered_servers(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    """Return media servers known to the SSDP integration, keyed by UDN."""
    servers: dict[str, dict[str, str]] = {}
    for st in ST_MEDIA_SERVERS:
        for info in await ssdp.async_get_discovery_info_by_st(hass, st):
            udn = info.ssdp_udn
            if not udn or not info.ssdp_location or udn in servers:
                continue
            servers[udn] = {
                SERVER_UDN: udn,
                SERVER_NAME: _friendly_name(info),
                SERVER_LOCATION: info.ssdp_location,
            }
    return servers


async def _async_validate_server(hass: HomeAssistant, url: str) -> dict[str, str]:
    """Connect to a manually entered server URL."""
    device = await async_create_device(hass, url)
    for dev in device.all_devices:
        if any(
            s.service_type.startswith("urn:schemas-upnp-org:service:ContentDirectory:")
            for s in dev.services.values()
        ):
            return {SERVER_UDN: dev.udn, SERVER_NAME: dev.friendly_name, SERVER_LOCATION: url}
    raise OpenHomeError("not a media server")


def _servers_schema(
    discovered: dict[str, dict[str, str]],
    selected: list[str],
    max_tracks: int | None = None,
) -> vol.Schema:
    options = [
        SelectOptionDict(value=udn, label=f"{cfg[SERVER_NAME]} ({cfg[SERVER_LOCATION]})")
        for udn, cfg in discovered.items()
    ]
    schema: dict[Any, Any] = {}
    if options:
        schema[vol.Optional(CONF_SERVER_SELECTION, default=selected)] = SelectSelector(
            SelectSelectorConfig(
                options=options, multiple=True, mode=SelectSelectorMode.LIST
            )
        )
    schema[vol.Optional(CONF_MANUAL_SERVERS, default="")] = TextSelector()
    if max_tracks is not None:
        schema[vol.Optional(CONF_MAX_TRACKS, default=max_tracks)] = NumberSelector(
            NumberSelectorConfig(min=1, max=1000, step=1, mode=NumberSelectorMode.BOX)
        )
    return vol.Schema(schema)


async def _async_process_servers(
    hass: HomeAssistant,
    user_input: dict[str, Any],
    discovered: dict[str, dict[str, str]],
    known: dict[str, dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Turn the servers form into the stored list; returns (servers, errors)."""
    errors: dict[str, str] = {}
    servers: list[dict[str, str]] = []
    for udn in user_input.get(CONF_SERVER_SELECTION, []):
        if cfg := discovered.get(udn) or known.get(udn):
            servers.append(cfg)
    manual = [
        u.strip()
        for u in str(user_input.get(CONF_MANUAL_SERVERS, "")).replace("\n", ",").split(",")
        if u.strip()
    ]
    for url in manual:
        try:
            cfg = await _async_validate_server(hass, url)
        except (*CONNECT_ERRORS, OpenHomeError):
            errors[CONF_MANUAL_SERVERS] = "cannot_connect_server"
            continue
        if all(s[SERVER_UDN] != cfg[SERVER_UDN] for s in servers):
            servers.append(cfg)
    if not servers and not errors:
        errors["base"] = "no_servers"
    return servers, errors


class DlnaMusikauswahlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Linn renderer together with its DLNA servers."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._renderer: dict[str, str] = {}
        self._discovered_renderers: dict[str, SsdpServiceInfo] = {}
        self._discovered_servers: dict[str, dict[str, str]] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return DlnaMusikauswahlOptionsFlow()

    async def _async_set_renderer(self, location: str) -> None:
        """Connect to a renderer and remember it (raises on failure)."""
        device = await async_create_device(self.hass, location)
        renderer = OpenHomeRenderer(device)
        await self.async_set_unique_id(renderer.udn)
        self._abort_if_unique_id_configured(
            updates={CONF_RENDERER_LOCATION: location}
        )
        self._renderer = {
            CONF_RENDERER_LOCATION: location,
            CONF_RENDERER_UDN: renderer.udn,
            CONF_RENDERER_NAME: renderer.name,
        }

    # ---------------------------------------------------------------- user
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the Linn renderer."""
        errors: dict[str, str] = {}
        if user_input is not None:
            choice = user_input.get(CONF_RENDERER, MANUAL)
            if choice == MANUAL:
                return await self.async_step_manual()
            try:
                await self._async_set_renderer(choice)
            except OpenHomeError:
                errors["base"] = "not_openhome"
            except CONNECT_ERRORS:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_servers()

        configured = self._async_current_ids(include_ignore=False)
        self._discovered_renderers = {}
        for info in await ssdp.async_get_discovery_info_by_st(
            self.hass, ST_OPENHOME_PLAYLIST
        ):
            if info.ssdp_location and info.ssdp_udn not in configured:
                self._discovered_renderers[info.ssdp_location] = info

        if not self._discovered_renderers:
            return await self.async_step_manual()

        options = [
            SelectOptionDict(value=location, label=_friendly_name(info))
            for location, info in self._discovered_renderers.items()
        ]
        options.append(SelectOptionDict(value=MANUAL, label="URL manuell eingeben / enter URL"))
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_RENDERER): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the description URL of the renderer manually."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_set_renderer(user_input[CONF_URL].strip())
            except OpenHomeError:
                errors["base"] = "not_openhome"
            except CONNECT_ERRORS:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_servers()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_URL): TextSelector()}),
            errors=errors,
        )

    # ---------------------------------------------------------------- ssdp
    async def async_step_ssdp(self, discovery_info: SsdpServiceInfo) -> ConfigFlowResult:
        """Handle a renderer found via SSDP."""
        await self.async_set_unique_id(discovery_info.ssdp_udn)
        self._abort_if_unique_id_configured()
        try:
            await self._async_set_renderer(discovery_info.ssdp_location)
        except (*CONNECT_ERRORS, OpenHomeError):
            return self.async_abort(reason="cannot_connect")
        self.context["title_placeholders"] = {"name": self._renderer[CONF_RENDERER_NAME]}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered renderer."""
        if user_input is not None:
            return await self.async_step_servers()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._renderer[CONF_RENDERER_NAME]},
        )

    # ------------------------------------------------------------- servers
    async def async_step_servers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the DLNA media servers."""
        errors: dict[str, str] = {}
        if user_input is not None:
            servers, errors = await _async_process_servers(
                self.hass, user_input, self._discovered_servers, {}
            )
            if not errors:
                return self.async_create_entry(
                    title=self._renderer[CONF_RENDERER_NAME],
                    data={**self._renderer, CONF_SERVERS: servers},
                )
        else:
            self._discovered_servers = await _async_discovered_servers(self.hass)

        return self.async_show_form(
            step_id="servers",
            data_schema=_servers_schema(
                self._discovered_servers, list(self._discovered_servers)
            ),
            errors=errors,
            description_placeholders={"name": self._renderer[CONF_RENDERER_NAME]},
        )


class DlnaMusikauswahlOptionsFlow(OptionsFlow):
    """Change the servers and the track limit."""

    def __init__(self) -> None:
        """Initialise."""
        self._discovered: dict[str, dict[str, str]] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        entry = self.config_entry
        current: list[dict[str, str]] = list(
            entry.options.get(CONF_SERVERS, entry.data.get(CONF_SERVERS, []))
        )
        known = {s[SERVER_UDN]: s for s in current}
        errors: dict[str, str] = {}

        if user_input is not None:
            servers, errors = await _async_process_servers(
                self.hass, user_input, self._discovered, known
            )
            if not errors:
                return self.async_create_entry(
                    data={
                        CONF_SERVERS: servers,
                        CONF_MAX_TRACKS: int(
                            user_input.get(CONF_MAX_TRACKS, DEFAULT_MAX_TRACKS)
                        ),
                        CONF_QOBUZ_SERVER: user_input.get(CONF_QOBUZ_SERVER, QOBUZ_AUTO),
                    }
                )
        else:
            discovered = await _async_discovered_servers(self.hass)
            # keep configured servers selectable even if currently offline
            self._discovered = {**known, **discovered}

        schema = _servers_schema(
            self._discovered,
            list(known),
            int(entry.options.get(CONF_MAX_TRACKS, DEFAULT_MAX_TRACKS)),
        )
        qobuz_options = [
            SelectOptionDict(value=QOBUZ_AUTO, label="Automatisch erkennen / auto-detect"),
            SelectOptionDict(value=QOBUZ_NONE, label="Kein Qobuz / no Qobuz"),
        ] + [
            SelectOptionDict(value=udn, label=cfg[SERVER_NAME])
            for udn, cfg in self._discovered.items()
        ]
        schema = schema.extend(
            {
                vol.Optional(
                    CONF_QOBUZ_SERVER,
                    default=entry.options.get(CONF_QOBUZ_SERVER, QOBUZ_AUTO),
                ): SelectSelector(
                    SelectSelectorConfig(options=qobuz_options, mode=SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
