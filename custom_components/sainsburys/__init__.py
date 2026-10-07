"""The Sainsbury's Groceries integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import CookieJar
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from pysainsburys import Sainsburys

from .auth import create_auth
from .const import CONF_SESSION, DOMAIN
from .coordinator import SainsburysDataUpdateCoordinator
from .data import SainsburysConfigEntry, SainsburysRuntimeData
from .llm_api import async_register_llm_api
from .services import async_setup_services

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up global Sainsbury's service actions."""
    del config
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SainsburysConfigEntry,
) -> bool:
    """Set up a Sainsbury's account from a config entry."""
    session = async_create_clientsession(
        hass,
        cookie_jar=CookieJar(unsafe=True),
    )
    auth = create_auth(entry.data[CONF_SESSION], session)
    client = Sainsburys(auth)
    coordinator = SainsburysDataUpdateCoordinator(hass, entry, client, auth)

    # The session is detached by Home Assistant when setup fails or unloads.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = SainsburysRuntimeData(client, coordinator, session)
    async_register_llm_api(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: SainsburysConfigEntry,
) -> bool:
    """Unload a Sainsbury's config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
