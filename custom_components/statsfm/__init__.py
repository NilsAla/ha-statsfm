"""The stats.fm integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import StatsFmClient
from .const import CONF_USER_ID
from .coordinator import StatsFmConfigEntry, StatsFmCoordinator, StatsFmRuntimeData
from .now_playing import StatsFmNowPlayingCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: StatsFmConfigEntry) -> bool:
    """Set up stats.fm from a config entry."""
    client = StatsFmClient(async_get_clientsession(hass), entry.data[CONF_USER_ID])
    stats = StatsFmCoordinator(hass, entry, client)
    await stats.async_config_entry_first_refresh()
    now_playing = StatsFmNowPlayingCoordinator(hass, entry, client)
    entry.runtime_data = StatsFmRuntimeData(stats=stats, now_playing=now_playing)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    now_playing.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StatsFmConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
