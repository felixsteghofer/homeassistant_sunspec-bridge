HACS-ready release of SunSpec Bridge, the companion integration that exposes an
existing SunSpec2 inverter connection to FEMS/OpenEMS over Modbus TCP.

Existing manually installed bridges retain their configuration and entity IDs:
install this repository through HACS and restart Home Assistant. Keep the
SunSpec2 integration installed and connected directly to the inverter.

Includes UI configuration/options, a diagnostic status sensor, stale-data expiry,
shared-connection model 123 control forwarding, tests, and lifecycle cleanup.
New installations default to localhost-only clients and disabled write forwarding.
Real inverter power-limit control remains unverified.
