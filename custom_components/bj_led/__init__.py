from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant

from .bjled import BJLEDInstance
from .const import CONF_DELAY, CONF_RESET, DOMAIN

LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BJ LED from a config entry."""
    reset = entry.options.get(CONF_RESET, entry.data.get(CONF_RESET, False))
    delay = entry.options.get(CONF_DELAY, entry.data.get(CONF_DELAY, 120))

    instance = BJLEDInstance(entry.data[CONF_MAC], reset, delay, hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = instance

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _async_stop(_event: Event) -> None:
        await instance.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        instance = hass.data[DOMAIN].pop(entry.entry_id)
        await instance.stop()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
