"""Bridge configuration."""

DOMAIN = "fems_sunspec_bridge"
DEFAULTS = {
    "listen_host": "0.0.0.0",
    "port": 502,
    "unit_id": 1,
    "max_age": 120,
    "allow_writes": False,
    "allowed_networks": "127.0.0.1/32",
}
