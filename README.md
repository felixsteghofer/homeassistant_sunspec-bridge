# SunSpec Bridge for Home Assistant

Expose the readings from an existing [SunSpec2](https://github.com/hilman2/ha-sunspec2)
integration as a local **Modbus TCP SunSpec server**. FEMS/OpenEMS can read from
Home Assistant while SunSpec2 remains the only connection to the physical inverter.

```text
KACO inverter ← SunSpec2 in Home Assistant → SunSpec Bridge ← FEMS/OpenEMS
                 one inverter connection       TCP server
```

Tested with a **KACO blueplanet 20.0 NX3 M2**, SunSpec2 **2026.10.16**, and
Home Assistant **2026.10.4**. This is a companion integration, not a replacement
for SunSpec2 or an official FENECON/KACO integration.

## Install with HACS

1. Install and configure `hilman2/ha-sunspec2` first. Confirm that its inverter
   readings are working.
2. In **HACS → ⋮ → Custom repositories**, add
   `https://github.com/felixsteghofer/homeassistant_sunspec-bridge` as **Integration**.
3. Download **SunSpec Bridge** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → SunSpec Bridge**.
5. Select the existing SunSpec2 source and configure the listener and allowed clients.
6. Point FEMS/OpenEMS, or an existing local port forward, at Home Assistant's
   Modbus TCP listener. Keep SunSpec2 connected directly to the inverter.

If you already have the manually installed `fems_sunspec_bridge`, download this
repository through HACS over that installation, then restart. **Do not delete or
re-create its config entry.** The integration domain, entry version, device
identifier, and status sensor unique ID are retained. Existing saved settings,
including write forwarding, override the defaults below.

For manual installation, copy `custom_components/fems_sunspec_bridge` into
Home Assistant's `/config/custom_components/`, check the configuration, and restart.

## Configuration

Every setting remains editable through the integration's **Configure** dialog.

| Setting | Default | Meaning |
| --- | --- | --- |
| Source | Existing SunSpec2 entry | Inverter and poller to reuse |
| Listen address | `0.0.0.0` | IPv4 interfaces on the HA host |
| TCP port | `502` | Modbus listener; must be unused on the HA host |
| Unit ID | `1` | Unit ID requested by FEMS/OpenEMS |
| Maximum age | `120` seconds | Expiry limit for measurements |
| Forward control writes | Off | Forward writable SunSpec model 123 points |
| Allowed client networks | `127.0.0.1/32` | Comma-separated source IPs/CIDRs |

Add your FEMS host's actual source IP to allowed networks, for example
`192.0.2.20/32`. If a router translates the source, also allow its translated
source address. Modbus TCP has no authentication: use this on your trusted local
network, not through a public Internet port forward.

The status device retains the name **KACO FEMS Bridge** for compatibility with the
original installation. Its diagnostic **Status** sensor reports `serving`,
`waiting_for_source`, or `stale`; attributes include read/write counts, exceptions,
last client, last snapshot, and last error. `serving` describes the cached register
image, not confirmation that FEMS has connected. Counters reset on bridge reload.
Download diagnostics from the integration's menu when reporting an issue.

## Data, timing, and controls

- FEMS reads cached register data; it does not trigger another physical inverter poll.
- Change polling speed in **SunSpec2's options**. The time required to read the
  inverter is added to its configured interval. A 5-second interval produced
  complete updates approximately every 17–24 seconds on the tested installation;
  this is not a guaranteed rate for other devices or networks.
- Model IDs, lengths, raw encodings, scaling factors, and offsets are preserved.
  The SunSpec map starts at register `40000`, as expected by the OpenEMS KACO driver.
  Source maps starting at 0 or 50000 are translated accordingly.
- Common identity comes from SunSpec2. Models selected by its poller are copied
  after successful updates. Models 120/121 are additionally read every 60 seconds
  through the same connection and transaction lock for FEMS nameplate/settings data.
- Measurements expire after the configured age. An expired read returns Modbus
  exception 11, not a fabricated zero. Layout and identity remain discoverable.
- FC3/FC4 reads and FC6/FC16 writes are supported. Writes are limited to writable
  points in model 123 and require explicit enablement. The bridge acknowledges a
  write only after the inverter acknowledges it. Readback uses real device data.
  Other control models, including model 121 writes, are unsupported.
- The protocol path for forwarding writes is covered by tests with fake callbacks.
  Physical power-limit control has **not** been verified on the tested installation.
- Source reloads are detected automatically. Data is cleared and refreshed, so a
  source reconfiguration can briefly interrupt reads. Disabling the bridge stops
  its listener; the SunSpec2 source and existing entities remain intact.

## Compatibility and limitations

Minimum Home Assistant version: **2026.10.0**. Install SunSpec2 separately; HACS
cannot install another custom integration as a Python dependency.

SunSpec2 has no public raw-register snapshot API. This adapter reads its private
model-layout/cache interfaces and serialized model objects, and uses its transaction
API for supplementary reads and writes. Recheck compatibility after SunSpec2
updates. Unsupported model payloads are not invented, and a source firmware or
layout change can require another compatibility check.

To undo a forwarding change, restore the router's original inverter destination.
To stop the bridge, disable its integration entry in Home Assistant. HACS manages
its files and updates independently of its saved configuration.

## Development

Use Python 3.14 with Home Assistant 2026.10.4 installed:

```sh
python -m pip install homeassistant==2026.10.4 ruff
python -m unittest discover -s tests -v
ruff check custom_components tests
ruff format --check custom_components tests
```

Tests cover protocol frames, fragmented/pipelined TCP traffic, client filtering,
stale data, SunSpec address translation, serialization, lifecycle cleanup, and
write acknowledgements without connecting to a physical inverter. GitHub Actions
runs the tests and HACS validation. Pushing a `vX.Y.Z` tag runs validation and
publishes a GitHub release containing `fems_sunspec_bridge.zip` for HACS.

After installation, run Home Assistant's built-in **Tools → YAML → Check
configuration** (or `ha core check`), then verify live FEMS readings and the
bridge's status attributes.

## License

[MIT](LICENSE).
