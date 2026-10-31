"""Downloadable bridge diagnostics without raw inverter data."""


async def async_get_config_entry_diagnostics(hass, entry):
    bridge = entry.runtime_data
    return {
        "settings": {
            key: value
            for key, value in bridge.settings.items()
            if key not in ("allowed_networks", "listen_host")
        },
        "layout": bridge.image.layout,
        "cached_registers": len(bridge.image.registers),
        "reads": bridge.server.reads,
        "writes": bridge.server.writes,
        "exceptions": bridge.server.errors,
        "last_error": bridge.server.last_error,
        "last_snapshot": bridge.last_snapshot,
    }
