from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from bluetooth_data_tools import human_readable_name
from home_assistant_bluetooth import BluetoothServiceInfo

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.const import CONF_MAC
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.device_registry import format_mac

from .bjled import BJLEDInstance
from .const import CONF_DELAY, CONF_RESET, DOMAIN

LOGGER = logging.getLogger(__name__)


class DeviceData:
    """Container for discovered BLE device information."""

    def __init__(self, discovery_info: BluetoothServiceInfoBleak) -> None:
        self._discovery = discovery_info

    def supported(self) -> bool:
        """Return if this discovery looks like a BJ LED device."""
        local_name = (self._discovery.name or "").strip()
        if not local_name:
            return False
        return local_name.lower().startswith("bj_led")

    def address(self) -> str:
        return self._discovery.address

    def get_device_name(self) -> str:
        return human_readable_name(None, self._discovery.name, self._discovery.address)

    def name(self) -> str:
        return human_readable_name(None, self._discovery.name, self._discovery.address)

    def _start_update(self, service_info: BluetoothServiceInfo) -> None:
        """Update from BLE advertisement data."""
        LOGGER.debug("Parsing BLE advertisement data: %s", service_info)


class BJLEDFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle BJ LED config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self.mac: str | None = None
        self.name: str | None = None
        self._instance: BJLEDInstance | None = None
        self._discovered_devices: list[DeviceData] = []

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle bluetooth discovery."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()

        device = DeviceData(discovery_info)
        self.context["title_placeholders"] = {"name": device.name()}

        if not device.supported():
            return self.async_abort(reason="not_supported")

        self._discovered_devices.append(device)
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm auto-discovered device."""
        self._set_confirm_only()
        return await self.async_step_user(user_input)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle picking a discovered device."""
        if user_input is not None:
            self.mac = user_input[CONF_MAC]
            self.name = self.context.get("title_placeholders", {}).get("name")

            if self.context.get("source") == config_entries.SOURCE_USER:
                for device in self._discovered_devices:
                    if device.address() == self.mac:
                        self.name = device.get_device_name()
                        break

            if self.name is None:
                self.name = f"BJ_LED {self.mac}"

            await self.async_set_unique_id(format_mac(self.mac), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return await self.async_step_validate()

        await self._async_collect_discovered_devices()

        if not self._discovered_devices:
            return await self.async_step_manual()

        mac_dict = {device.address(): device.name() for device in self._discovered_devices}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_MAC): vol.In(mac_dict)}),
            errors={},
        )

    async def _async_collect_discovered_devices(self) -> None:
        """Refresh discovered devices list with currently known bluetooth devices."""
        current_addresses = self._async_current_ids()
        known = {device.address() for device in self._discovered_devices}

        for discovery_info in async_discovered_service_info(self.hass):
            formatted_address = format_mac(discovery_info.address)
            if formatted_address in current_addresses or discovery_info.address in known:
                continue

            device = DeviceData(discovery_info)
            if device.supported():
                self._discovered_devices.append(device)
                known.add(device.address())

    async def async_step_validate(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Blink the strip so the user can validate the selected device."""
        if user_input is not None:
            if user_input["flicker"]:
                return self.async_create_entry(
                    title=self.name,
                    data={CONF_MAC: self.mac, "name": self.name},
                )
            return self.async_abort(reason="cannot_validate")

        error = await self.toggle_light()
        if error:
            return self.async_show_form(
                step_id="validate",
                data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
                errors={"base": "connect"},
            )

        return self.async_show_form(
            step_id="validate",
            data_schema=vol.Schema({vol.Required("flicker"): bool}),
            errors={},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manual fallback when a device cannot be auto-discovered by name."""
        if user_input is not None:
            self.mac = user_input[CONF_MAC]
            self.name = user_input["name"]
            await self.async_set_unique_id(format_mac(self.mac))
            self._abort_if_unique_id_configured()
            return await self.async_step_validate()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC): str,
                    vol.Required("name", default="BJ_LED"): str,
                }
            ),
            errors={},
        )

    async def toggle_light(self) -> Exception | None:
        """Try toggling light to validate connectivity."""
        if not self._instance:
            self._instance = BJLEDInstance(self.mac, False, 120, self.hass)

        try:
            await self._instance.update()
            await self._instance.turn_on()
            await asyncio.sleep(1)
            await self._instance.turn_off()
            await asyncio.sleep(1)
            await self._instance.turn_on()
            await asyncio.sleep(1)
            await self._instance.turn_off()
        except Exception as err:  # noqa: BLE001
            return err
        finally:
            await self._instance.stop()

        return None

    @staticmethod
    @callback
    def async_get_options_flow(_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow."""

    async def async_step_init(self, _user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        options = self.config_entry.options or {
            CONF_RESET: False,
            CONF_DELAY: 120,
        }
        if user_input is not None:
            return self.async_create_entry(title="", data={**options, **user_input})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DELAY,
                        default=options.get(CONF_DELAY, 120),
                    ): int
                }
            ),
            errors={},
        )
