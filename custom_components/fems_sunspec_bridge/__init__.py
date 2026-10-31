"""Expose SunSpec2's polled registers to FEMS without another inverter socket.

SunSpec2 has no public register-image API. The adapter intentionally isolates
its three private interfaces (_model_structure, _base_addr, wrapper._models).
An incompatible upstream change fails setup instead of fabricating readings.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval

from .const import DEFAULTS, DOMAIN
from .protocol import ModbusError, ModbusServer, RegisterImage

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    bridge = SunSpecBridge(hass, entry)
    try:
        await bridge.start()
    except (OSError, AttributeError, ValueError, RuntimeError) as error:
        await bridge.close()
        raise ConfigEntryNotReady(str(error)) from error
    entry.runtime_data = bridge
    try:
        await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    except BaseException:
        await bridge.close()
        raise
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, ["sensor"]):
        await entry.runtime_data.close()
        return True
    return False


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


class SunSpecBridge:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.settings = {**DEFAULTS, **entry.data, **entry.options}
        self.image = RegisterImage(self.settings["max_age"])
        self.server = ModbusServer(
            self.image,
            self.settings["unit_id"],
            self.write,
            self.settings["allowed_networks"],
            self.changed,
        )
        self.source = None
        self.source_entry = None
        self.source_unsubscribe = None
        self.timer_unsubscribe = None
        self.stop_unsubscribe = None
        self.listeners = set()
        self.last_snapshot = None
        self.last_read_stamp = None
        self.next_supplement = 0
        self.source_base = 40000
        self.maintenance_lock = asyncio.Lock()
        self.closed = False
        self.maintenance_task = None

    async def start(self):
        source_entry = self.hass.config_entries.async_get_entry(self.settings["source_entry_id"])
        if source_entry is None or source_entry.state is not ConfigEntryState.LOADED:
            raise RuntimeError("Selected SunSpec2 source is not loaded")
        if source_entry.domain != "sunspec2":
            raise ValueError("Selected source must be a SunSpec2 config entry")
        api = source_entry.runtime_data.api
        if not all(
            hasattr(api, name)
            for name in (
                "_model_structure",
                "_base_addr",
                "_last_model_read_end",
                "async_get_data",
                "async_write_block",
                "async_read_block",
            )
        ):
            raise RuntimeError("This SunSpec2 version has no supported register layout API")
        await self.server.start(self.settings["listen_host"], self.settings["port"])
        self.timer_unsubscribe = async_track_time_interval(
            self.hass, self.maintain, timedelta(seconds=5)
        )
        self.stop_unsubscribe = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.stop)
        # Source refreshes may take longer than a config-flow HTTP request.
        # Start the listener promptly and populate it in a managed background task.
        self.maintenance_task = self.hass.async_create_background_task(
            self.maintain(), "SunSpec bridge initial refresh"
        )
        _LOGGER.info(
            "FEMS bridge listening on %s:%s, unit %s",
            self.settings["listen_host"],
            self.settings["port"],
            self.settings["unit_id"],
        )

    async def stop(self, event):
        await self.close()

    async def close(self):
        self.closed = True
        for unsubscribe in (self.source_unsubscribe, self.timer_unsubscribe, self.stop_unsubscribe):
            if unsubscribe is not None:
                unsubscribe()
        self.source_unsubscribe = self.timer_unsubscribe = self.stop_unsubscribe = None
        task = self.maintenance_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.server.close()

    @callback
    def changed(self):
        for listener in list(self.listeners):
            listener()

    def subscribe(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    @callback
    def snapshot(self):
        source = self.source
        if self.closed or source is None or not source.last_update_success:
            return
        stamp = source.api._last_model_read_end
        if stamp is None or stamp == self.last_read_stamp:
            return
        try:
            self.prepare_layout()
            now = time.monotonic()
            for wrapper in (source.data or {}).values():
                self.store_wrapper(wrapper, now)
            # Model 1 is read by SunSpec2 for identity, outside coordinator.data.
            if source.device_info is not None:
                self.store_wrapper(source.device_info, None)
            self.last_read_stamp = stamp
            self.last_snapshot = time.time()
        except Exception as error:  # A third-party encoder must never break source polling.
            self.server.last_error = f"Cannot snapshot SunSpec2: {error}"
            _LOGGER.error("Cannot snapshot SunSpec2 registers: %s", error)
        self.changed()

    def prepare_layout(self):
        structure = self.source.api._model_structure
        base = self.source.api._base_addr
        if not structure or base is None:
            raise ValueError("SunSpec2 has not discovered a complete model layout yet")
        layout = (base, tuple(tuple(item) for item in structure))
        if layout == self.image.layout:
            return
        # OpenEMS probes SunS at 40000; translate sources at 0 or 50000 too.
        self.source_base = base
        registers = RegisterImage(self.settings["max_age"])
        registers.layout = layout
        registers.put(40000, b"SunS", None)
        end = 40002
        for model_id, address, length in sorted(structure, key=lambda item: item[1]):
            mapped = address - base + 40000
            if mapped != end or length < 0 or mapped + length + 2 > 65534:
                raise ValueError("Non-contiguous or invalid SunSpec layout")
            registers.put(mapped, struct.pack(">HH", model_id, length), None)
            end = mapped + length + 2
        registers.put(end, struct.pack(">HH", 65535, 0), None)
        self.image.registers = registers.registers
        self.image.layout = layout
        self.image.write_addresses.clear()
        self.next_supplement = 0

    def store_wrapper(self, wrapper, updated):
        for model in wrapper._models:
            address = model.model_addr - self.source_base + 40000
            data = bytes(model.get_mb())
            if len(data) != (model.model_len + 2) * 2:
                raise ValueError(f"Model {model.model_id}: incomplete register serialization")
            if struct.unpack(">HH", data[:4]) != (model.model_id, model.model_len):
                raise ValueError(f"Model {model.model_id}: inconsistent header")
            # Headers are structural and do not expire.
            self.image.put(address, data[:4], None)
            self.image.put(address + 2, data[4:], updated)
            if model.model_id == 123:
                for point in model.points.values():
                    if str(point.pdef.get("access", "")).lower() == "rw":
                        self.image.write_addresses.update(
                            range(address + point.offset, address + point.offset + int(point.len))
                        )

    async def maintain(self, now=None):
        if self.closed or self.maintenance_lock.locked():
            return
        async with self.maintenance_lock:
            self.maintenance_task = asyncio.current_task()
            entry = self.hass.config_entries.async_get_entry(self.settings["source_entry_id"])
            current = (
                entry.runtime_data if entry and entry.state is ConfigEntryState.LOADED else None
            )
            if current is not self.source:
                if self.source_unsubscribe is not None:
                    self.source_unsubscribe()
                    self.source_unsubscribe = None
                self.source, self.source_entry = current, entry
                self.last_read_stamp = None
                self.image.registers.clear()
                self.image.layout = ()
                self.image.write_addresses.clear()
                self.next_supplement = 0
                if current is not None:
                    self.source_unsubscribe = current.async_add_listener(self.snapshot)
                    # Obtain a fresh cycle; do not re-date data retained across failures.
                    await current.async_request_refresh()
                    self.snapshot()
            if (
                current is not None
                and current.last_update_success
                and time.monotonic() >= self.next_supplement
            ):
                self.next_supplement = time.monotonic() + 60
                try:
                    # FEMS reads nameplate/settings too. SunSpec2's default UI selection
                    # omits them; read only those via its existing lock/session.
                    async with current.async_write_transaction():
                        for model_id in (120, 121):
                            if model_id in current.detected_models:
                                wrapper = await current.api.async_get_data(model_id)
                                if not self.closed:
                                    self.prepare_layout()
                                    self.store_wrapper(wrapper, time.monotonic())
                except Exception as error:
                    self.server.last_error = f"Supplementary model read failed: {error}"
                    _LOGGER.warning("FEMS supplementary model read failed: %s", error)
            self.changed()

    async def write(self, address, data):
        if not self.settings["allow_writes"]:
            raise ModbusError(1)
        entry = self.hass.config_entries.async_get_entry(self.settings["source_entry_id"])
        if (
            self.closed
            or self.source is None
            or entry is None
            or entry.state is not ConfigEntryState.LOADED
            or entry.runtime_data is not self.source
        ):
            raise ModbusError(11)
        # Reject writes against an expired register image.
        self.image.read(address, len(data) // 2)
        source_address = address - 40000 + self.source_base
        async with self.source.async_write_transaction():
            await self.source.api.async_write_block(source_address, data)
            # The acknowledgement above is real. Do not fabricate a readback value.
            for index in range(address, address + len(data) // 2):
                self.image.registers.pop(index, None)
            try:
                verified = await self.source.api.async_read_block(source_address, len(data) // 2)
                self.image.put(address, verified, time.monotonic())
            except Exception as error:
                _LOGGER.warning("FEMS write acknowledged but readback failed: %s", error)
        self.hass.async_create_task(self.source.async_request_refresh())
