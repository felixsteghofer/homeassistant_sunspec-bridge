"""UI-visible bridge status and diagnostics."""

from datetime import UTC, datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BridgeStatus(entry)])


class BridgeStatus(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:transit-connection-variant"
    _attr_should_poll = False

    def __init__(self, entry):
        self.bridge = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "KACO FEMS Bridge",
            "manufacturer": "Local",
            "model": "SunSpec2 register bridge",
        }

    async def async_added_to_hass(self):
        self.async_on_remove(self.bridge.subscribe(self.async_write_ha_state))

    @property
    def native_value(self):
        import time

        registers = self.bridge.image.registers
        fresh = [r.updated for r in registers.values() if r.updated is not None]
        if not fresh:
            return "waiting_for_source"
        return "serving" if time.monotonic() - max(fresh) <= self.bridge.image.max_age else "stale"

    @property
    def extra_state_attributes(self):
        server = self.bridge.server
        return {
            "listen_host": self.bridge.settings["listen_host"],
            "port": self.bridge.settings["port"],
            "unit_id": server.unit,
            "source_entry_id": self.bridge.settings["source_entry_id"],
            "source_base_address": self.bridge.source_base,
            "max_age_seconds": self.bridge.settings["max_age"],
            "allow_writes": self.bridge.settings["allow_writes"],
            "cached_registers": len(self.bridge.image.registers),
            "clients": len(server.clients),
            "read_requests": server.reads,
            "write_requests": server.writes,
            "exceptions": server.errors,
            "last_client": server.last_client,
            "last_error": server.last_error,
            "last_request": datetime.fromtimestamp(server.last_request, UTC).isoformat()
            if server.last_request
            else None,
            "last_snapshot": datetime.fromtimestamp(self.bridge.last_snapshot, UTC).isoformat()
            if self.bridge.last_snapshot
            else None,
        }
