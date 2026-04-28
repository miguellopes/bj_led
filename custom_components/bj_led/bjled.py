from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar, cast

from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTServiceCollection
from bleak.exc import BleakDBusError
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS as BLEAK_EXCEPTIONS
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)

from homeassistant.components import bluetooth
from homeassistant.components.light import ColorMode
from homeassistant.exceptions import ConfigEntryNotReady

LOGGER = logging.getLogger(__name__)

EFFECT_MAP = {
    "Colorloop": (0x03, 0x00),
    "Red fade": (0x03, 0x01),
    "Green fade": (0x03, 0x02),
    "Blue fade": (0x03, 0x03),
    "Yellow fade": (0x03, 0x04),
    "Cyan fade": (0x03, 0x05),
    "Magenta fade": (0x03, 0x06),
    "White fade": (0x03, 0x07),
    "Red green cross fade": (0x03, 0x08),
    "Red blue cross fade": (0x03, 0x09),
    "Green blue cross fade": (0x03, 0x0A),
    "Rainbow fade": (0x03, 0x0B),
    "Color strobe": (0x03, 0x0C),
    "Red strobe": (0x03, 0x0D),
    "Green strobe": (0x03, 0x0E),
    "Blue strobe": (0x03, 0x0F),
    "Yellow strobe": (0x03, 0x10),
    "Cyan strobe": (0x03, 0x11),
    "Magenta strobe": (0x03, 0x12),
    "White strobe": (0x03, 0x13),
    "Color jump": (0x03, 0x14),
    "RGB jump": (0x03, 0x15),
}

EFFECT_LIST = sorted(EFFECT_MAP)
NAME_PREFIXES = ("BJ_LED_M", "BJ_LED")
WRITE_CHARACTERISTIC_UUID = "0000ee01-0000-1000-8000-00805f9b34fb"
TURN_ON_CMD = bytearray.fromhex("69 96 02 01 01")
TURN_OFF_CMD = bytearray.fromhex("69 96 02 01 00")
DEFAULT_ATTEMPTS = 3
BLEAK_BACKOFF_TIME = 0.25
RETRY_BACKOFF_EXCEPTIONS = (BleakDBusError,)

WrapFuncType = TypeVar("WrapFuncType", bound=Callable[..., Any])


def retry_bluetooth_connection_error(func: WrapFuncType) -> WrapFuncType:
    """Retry wrapper for transient BLE errors."""

    async def _async_wrap_retry_bluetooth_connection_error(
        self: "BJLEDInstance", *args: Any, **kwargs: Any
    ) -> Any:
        max_attempts = DEFAULT_ATTEMPTS - 1
        for attempt in range(DEFAULT_ATTEMPTS):
            try:
                return await func(self, *args, **kwargs)
            except BleakNotFoundError:
                raise
            except RETRY_BACKOFF_EXCEPTIONS as err:
                if attempt >= max_attempts:
                    raise
                LOGGER.debug(
                    "%s: %s while calling %s. Backing off %ss (%s/%s)",
                    self.name,
                    type(err),
                    func,
                    BLEAK_BACKOFF_TIME,
                    attempt + 1,
                    DEFAULT_ATTEMPTS,
                    exc_info=True,
                )
                await asyncio.sleep(BLEAK_BACKOFF_TIME)
            except BLEAK_EXCEPTIONS:
                if attempt >= max_attempts:
                    raise
                LOGGER.debug(
                    "%s: retrying %s (%s/%s)",
                    self.name,
                    func,
                    attempt + 1,
                    DEFAULT_ATTEMPTS,
                    exc_info=True,
                )

        return None

    return cast(WrapFuncType, _async_wrap_retry_bluetooth_connection_error)


class BJLEDInstance:
    """BJ LED Bluetooth device driver."""

    def __init__(self, address: str, reset: bool, delay: int, hass) -> None:
        self.loop = asyncio.get_running_loop()
        self._mac = address
        self._reset = reset
        self._delay = delay
        self._hass = hass
        self._device: BLEDevice | None = bluetooth.async_ble_device_from_address(
            self._hass, address, connectable=True
        )
        if not self._device:
            raise ConfigEntryNotReady(
                "Bluetooth device not found. Ensure Bluetooth is configured in Home Assistant "
                f"and the device {address} is in range."
            )

        self._connect_lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._cached_services: BleakGATTServiceCollection | None = None
        self._expected_disconnect = False
        self._is_on: bool | None = None
        self._rgb_color: tuple[int, int, int] | None = None
        self._brightness = 255
        self._effect: str | None = None
        self._color_mode = ColorMode.RGB
        self._write_uuid = None

        self._detect_model()

    def _detect_model(self) -> None:
        """Detect supported model. Fall back safely if BT name is missing."""
        device_name = (self._device.name or "").strip()

        if not device_name:
            LOGGER.warning(
                "Device %s does not advertise a Bluetooth name. Falling back to generic BJ LED protocol.",
                self._device.address,
            )
            return

        if any(device_name.lower().startswith(prefix.lower()) for prefix in NAME_PREFIXES):
            return

        LOGGER.warning(
            "Unrecognized BJ LED name %r for %s. Using generic protocol.",
            device_name,
            self._device.address,
        )

    async def _write(self, data: bytearray) -> None:
        await self._ensure_connected()
        await self._write_while_connected(data)

    async def _write_while_connected(self, data: bytearray) -> None:
        LOGGER.debug("Writing data to %s: %s", self.name, data.hex())
        assert self._client is not None
        await self._client.write_gatt_char(self._write_uuid, data, False)

    @property
    def mac(self) -> str:
        return self._device.address

    @property
    def reset(self) -> bool:
        return self._reset

    @property
    def name(self) -> str:
        return self._device.name or f"BJ_LED_{self._device.address}"

    @property
    def rssi(self) -> int | None:
        return self._device.rssi

    @property
    def is_on(self) -> bool | None:
        return self._is_on

    @property
    def brightness(self) -> int:
        return self._brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._rgb_color

    @property
    def effect_list(self) -> list[str]:
        return EFFECT_LIST

    @property
    def effect(self) -> str | None:
        return self._effect

    @property
    def color_mode(self) -> ColorMode:
        return self._color_mode

    @retry_bluetooth_connection_error
    async def set_rgb_color(
        self, rgb: tuple[int, int, int] | None, brightness: int | None = None
    ) -> None:
        if rgb is None:
            rgb = self._rgb_color or (255, 255, 255)

        self._rgb_color = rgb
        if brightness is not None:
            self._brightness = brightness

        scale = self._brightness / 255
        red = int(rgb[0] * scale)
        green = int(rgb[1] * scale)
        blue = int(rgb[2] * scale)

        packet = bytearray.fromhex("69 96 05 02")
        packet.extend((red, green, blue))
        await self._write(packet)

    async def set_brightness_local(self, value: int) -> None:
        self._brightness = value
        await self.set_rgb_color(self._rgb_color or (255, 255, 255), value)

    @retry_bluetooth_connection_error
    async def turn_on(self) -> None:
        await self._write(TURN_ON_CMD)
        self._is_on = True

    @retry_bluetooth_connection_error
    async def turn_off(self) -> None:
        await self._write(TURN_OFF_CMD)
        self._is_on = False

    @retry_bluetooth_connection_error
    async def set_effect(self, effect: str) -> None:
        if effect not in EFFECT_MAP:
            LOGGER.error("Effect %s not supported", effect)
            return

        self._effect = effect
        mode, effect_id = EFFECT_MAP[effect]
        packet = bytearray.fromhex("69 96 03")
        packet.extend((mode, effect_id, 0x03))
        await self._write(packet)

    @retry_bluetooth_connection_error
    async def update(self) -> None:
        LOGGER.debug("%s: optimistic state update", self.name)

    async def _ensure_connected(self) -> None:
        if self._client and self._client.is_connected:
            self._reset_disconnect_timer()
            return

        async with self._connect_lock:
            if self._client and self._client.is_connected:
                self._reset_disconnect_timer()
                return

            LOGGER.debug("%s: connecting", self.name)
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._device,
                self.name,
                self._disconnected,
                cached_services=self._cached_services,
                ble_device_callback=lambda: self._device,
            )

            if not self._resolve_characteristics(client.services):
                raise ConfigEntryNotReady(
                    f"Required write characteristic not found for {self._device.address}"
                )

            self._cached_services = client.services
            self._client = client
            self._reset_disconnect_timer()

    def _resolve_characteristics(self, services: BleakGATTServiceCollection) -> bool:
        if char := services.get_characteristic(WRITE_CHARACTERISTIC_UUID):
            self._write_uuid = char
            return True
        return False

    def _reset_disconnect_timer(self) -> None:
        if self._disconnect_timer:
            self._disconnect_timer.cancel()

        self._expected_disconnect = False
        if self._delay:
            self._disconnect_timer = self.loop.call_later(self._delay, self._disconnect)

    def _disconnected(self, _client: BleakClientWithServiceCache) -> None:
        if self._expected_disconnect:
            LOGGER.debug("%s: disconnected", self.name)
        else:
            LOGGER.warning("%s: disconnected unexpectedly", self.name)

    def _disconnect(self) -> None:
        self._disconnect_timer = None
        asyncio.create_task(self._execute_disconnect())

    async def stop(self) -> None:
        await self._execute_disconnect()

    async def _execute_disconnect(self) -> None:
        async with self._connect_lock:
            client = self._client
            self._expected_disconnect = True
            self._client = None
            self._write_uuid = None
            if client and client.is_connected:
                await client.disconnect()
