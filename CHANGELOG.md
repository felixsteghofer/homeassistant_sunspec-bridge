# Changelog

## 0.2.2

- Close accepted Modbus clients before awaiting server shutdown, preventing
  integration unload/restart from hanging with an open FEMS connection.
- Add a regression test using an idle connected Modbus client.

## 0.2.1

- Avoid removing Home Assistant's consumed one-shot shutdown listener a second time.
- Add a regression test for shutdown-event cleanup.

## 0.2.0

- Publish the existing bridge as a standalone, HACS-managed Home Assistant integration.
- Keep the existing `fems_sunspec_bridge` domain, config entries, and entity identifiers.
- Add HACS metadata, install/update documentation, translations, tests, and release automation.
- Populate the register cache in a managed background task so setup does not wait for inverter polling.
- Clean up sockets and background work when setup fails or the integration unloads.
- Prevent overlapping listener settings and isolate third-party serialization failures from source polling.
- Default new installations to localhost-only clients and disabled write forwarding;
  preserve all existing saved settings.

## 0.1.0

Initial local deployment: serve SunSpec2 register snapshots to FEMS over Modbus TCP,
with stale-data expiry, shared-connection control forwarding, and UI configuration.
