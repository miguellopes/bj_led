from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo

from .bjled import BJLEDInstance
from .const import DOMAIN

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities) -> None:
    instance = hass.data[DOMAIN][config_entry.entry_id]
    await instance.update()
    async_add_entities([BJLEDLight(instance, config_entry.data["name"], config_entry.entry_id)])


class BJLEDLight(LightEntity):
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT | LightEntityFeature.FLASH
    _attr_brightness_step_pct = 10
    _attr_should_poll = False

    def __init__(self, bjledinstance: BJLEDInstance, name: str, entry_id: str) -> None:
        self._instance = bjledinstance
        self._entry_id = entry_id
        self._attr_name = name
        self._attr_unique_id = self._instance.mac

    @property
    def available(self) -> bool:
        return True

    @property
    def brightness(self) -> int:
        return self._instance.brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._instance.rgb_color

    @property
    def is_on(self) -> bool | None:
        return self._instance.is_on

    @property
    def effect_list(self) -> list[str]:
        return self._instance.effect_list

    @property
    def effect(self) -> str | None:
        return self._instance.effect

    @property
    def color_mode(self) -> ColorMode:
        return self._instance.color_mode

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._instance.mac)},
            name=self._attr_name,
            connections={(dr.CONNECTION_NETWORK_MAC, self._instance.mac)},
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self.is_on:
            await self._instance.turn_on()

        if ATTR_BRIGHTNESS in kwargs and kwargs[ATTR_BRIGHTNESS] != self.brightness:
            await self._instance.set_brightness_local(kwargs[ATTR_BRIGHTNESS])

        if ATTR_RGB_COLOR in kwargs and kwargs[ATTR_RGB_COLOR] != self.rgb_color:
            bri = kwargs.get(ATTR_BRIGHTNESS)
            await self._instance.set_rgb_color(kwargs[ATTR_RGB_COLOR], bri)

        if ATTR_EFFECT in kwargs and kwargs[ATTR_EFFECT] != self.effect:
            await self._instance.set_effect(kwargs[ATTR_EFFECT])

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._instance.turn_off()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        await self._instance.update()
        self.async_write_ha_state()
