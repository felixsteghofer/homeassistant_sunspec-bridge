"""UI setup and editable options for the FEMS bridge."""

import ipaddress

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DEFAULTS, DOMAIN


def schema(hass, values):
    sources = {
        entry.entry_id: entry.title for entry in hass.config_entries.async_entries("sunspec2")
    }
    defaults = {**DEFAULTS, **values}
    return vol.Schema(
        {
            vol.Required(
                "source_entry_id", default=defaults.get("source_entry_id", next(iter(sources), ""))
            ): vol.In(sources),
            vol.Required("listen_host", default=defaults["listen_host"]): str,
            vol.Required("port", default=defaults["port"]): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required("unit_id", default=defaults["unit_id"]): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=247)
            ),
            vol.Required("max_age", default=defaults["max_age"]): vol.All(
                vol.Coerce(int), vol.Range(min=10, max=600)
            ),
            vol.Required("allow_writes", default=defaults["allow_writes"]): bool,
            vol.Required("allowed_networks", default=defaults["allowed_networks"]): str,
        }
    )


def validate(values):
    ipaddress.ip_address(values["listen_host"])
    networks = [part.strip() for part in values["allowed_networks"].split(",") if part.strip()]
    if not networks:
        raise ValueError("At least one allowed network is required")
    for network in networks:
        ipaddress.ip_network(network)


def listener_in_use(hass, values, excluded_entry_id=None):
    """Catch overlapping wildcard listeners before replacing working options."""
    host = ipaddress.ip_address(values["listen_host"])
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == excluded_entry_id:
            continue
        other = {**DEFAULTS, **entry.data, **entry.options}
        other_host = ipaddress.ip_address(other["listen_host"])
        if (
            values["port"] == other["port"]
            and host.version == other_host.version
            and (host == other_host or host.is_unspecified or other_host.is_unspecified)
        ):
            return True
    return False


class BridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if not self.hass.config_entries.async_entries("sunspec2"):
            return self.async_abort(reason="no_source")
        if user_input is not None:
            try:
                validate(user_input)
            except ValueError:
                errors["base"] = "invalid_network"
            else:
                if listener_in_use(self.hass, user_input):
                    return self.async_abort(reason="listener_in_use")
                await self.async_set_unique_id(f"{user_input['listen_host']}:{user_input['port']}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="SunSpec → FEMS", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=schema(self.hass, user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BridgeOptionsFlow()


class BridgeOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                validate(user_input)
            except ValueError:
                errors["base"] = "invalid_network"
            else:
                if listener_in_use(self.hass, user_input, self.config_entry.entry_id):
                    errors["base"] = "listener_in_use"
                else:
                    return self.async_create_entry(title="", data=user_input)
        values = {**self.config_entry.data, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init", data_schema=schema(self.hass, values), errors=errors
        )
