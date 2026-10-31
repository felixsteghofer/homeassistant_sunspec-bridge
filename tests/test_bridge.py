"""Protocol and adapter tests; no physical inverter or writes required."""

import asyncio
import ipaddress
import struct
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.fems_sunspec_bridge import SunSpecBridge
from custom_components.fems_sunspec_bridge.config_flow import listener_in_use, validate
from custom_components.fems_sunspec_bridge.const import DEFAULTS
from custom_components.fems_sunspec_bridge.protocol import ModbusError, ModbusServer, RegisterImage


class ImageTests(unittest.TestCase):
    def test_stale_measurements_expire_but_identity_does_not(self):
        image = RegisterImage(120)
        image.put(40000, b"SunS", None)
        image.put(40002, b"\x00\x01", time.monotonic() - 121)
        self.assertEqual(image.read(40000, 2), b"SunS")
        with self.assertRaises(ModbusError) as caught:
            image.read(40002, 1)
        self.assertEqual(caught.exception.code, 11)

    def test_holes_and_illegal_lengths(self):
        image = RegisterImage(120)
        for address, count, code in [(0, 1, 2), (0, 0, 3), (0, 126, 3), (65535, 2, 3)]:
            with self.assertRaises(ModbusError) as caught:
                image.read(address, count)
            self.assertEqual(caught.exception.code, code)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.image = RegisterImage(120)
        self.image.put(40000, b"SunS", None)
        self.image.write_addresses = {40295, 40296}
        self.write = AsyncMock()
        self.server = ModbusServer(self.image, 1, self.write, "127.0.0.1/32")

    async def asyncTearDown(self):
        await self.server.close()

    async def test_read_and_invalid_pdu(self):
        self.assertEqual(
            await self.server.process(struct.pack(">BHH", 3, 40000, 2)), b"\x03\x04SunS"
        )
        self.assertEqual(await self.server.process(b"\x03"), b"\x83\x03")
        self.assertEqual(await self.server.process(b"\x05\x00\x00\x00\x00"), b"\x85\x01")

    async def test_writes_require_valid_range_and_real_acknowledgement(self):
        pdu = struct.pack(">BHH", 6, 40295, 7000)
        self.assertEqual(await self.server.process(pdu), pdu)
        self.write.assert_awaited_once_with(40295, struct.pack(">H", 7000))
        self.assertEqual(await self.server.process(struct.pack(">BHH", 6, 40000, 1)), b"\x86\x02")
        self.write.side_effect = OSError("upstream failed")
        self.assertEqual(await self.server.process(pdu), b"\x86\x0b")
        self.assertEqual(self.server.writes, 1)

    async def test_fc16_validation(self):
        pdu = struct.pack(">BHHBHH", 16, 40295, 2, 4, 1, 2)
        self.assertEqual(await self.server.process(pdu), struct.pack(">BHH", 16, 40295, 2))
        self.assertEqual(await self.server.process(pdu[:-1]), b"\x90\x03")
        self.assertEqual(
            await self.server.process(struct.pack(">BHHB", 16, 40295, 0, 0)), b"\x90\x03"
        )

    async def test_fragmented_and_pipelined_tcp_and_wrong_unit(self):
        await self.server.start("127.0.0.1", 0)
        port = self.server.server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            frame = struct.pack(">HHHBBHH", 42, 0, 6, 1, 3, 40000, 2)
            writer.write(frame[:3])
            await writer.drain()
            await asyncio.sleep(0)
            writer.write(frame[3:] + frame)
            await writer.drain()
            expected = struct.pack(">HHHB", 42, 0, 7, 1) + b"\x03\x04SunS"
            self.assertEqual(await reader.readexactly(26), expected + expected)
            writer.write(struct.pack(">HHHBBHH", 43, 0, 6, 2, 3, 40000, 2))
            await writer.drain()
            self.assertEqual(
                await reader.readexactly(9), struct.pack(">HHHB", 43, 0, 3, 2) + b"\x83\x0b"
            )
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_unload_closes_an_idle_connected_client_without_hanging(self):
        await self.server.start("127.0.0.1", 0)
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", self.server.server.sockets[0].getsockname()[1]
        )
        try:
            await asyncio.sleep(0)
            async with asyncio.timeout(1):
                await self.server.close()
            self.assertEqual(await reader.read(), b"")
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_acl_rejects_unlisted_client(self):
        self.server.networks = [ipaddress.ip_network("192.0.2.0/24")]
        await self.server.start("127.0.0.1", 0)
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", self.server.server.sockets[0].getsockname()[1]
        )
        try:
            self.assertEqual(await reader.read(), b"")
        finally:
            writer.close()
            await writer.wait_closed()


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.bridge = SunSpecBridge(SimpleNamespace(), SimpleNamespace(data={}, options={}))
        self.bridge.source = SimpleNamespace(
            api=SimpleNamespace(
                _model_structure=[(1, 2, 2), (103, 6, 2), (123, 10, 2)], _base_addr=0
            )
        )

    def test_source_base_translation_and_complete_chain(self):
        self.bridge.prepare_layout()
        self.assertEqual(self.bridge.image.read(40000, 4), b"SunS" + struct.pack(">HH", 1, 2))
        self.assertEqual(self.bridge.image.read(40014, 2), b"\xff\xff\x00\x00")
        with self.assertRaises(ModbusError):
            self.bridge.image.read(40004, 1)

    def test_layout_change_clears_old_measurements_and_write_addresses(self):
        self.bridge.prepare_layout()
        self.bridge.image.put(40004, b"\x00\x08", time.monotonic())
        self.bridge.image.write_addresses.add(40012)
        self.bridge.source.api._model_structure = [(1, 2, 3)]
        self.bridge.prepare_layout()
        self.assertNotIn(40004, self.bridge.image.registers)
        self.assertFalse(self.bridge.image.write_addresses)

    def test_preserves_register_values_scaling_and_writable_offsets(self):
        self.bridge.prepare_layout()
        data = struct.pack(">HHHH", 123, 2, 7000, 1)
        model = SimpleNamespace(
            model_addr=10,
            model_id=123,
            model_len=2,
            get_mb=lambda: data,
            points={"limit": SimpleNamespace(pdef={"access": "RW"}, offset=2, len=1)},
        )
        self.bridge.store_wrapper(SimpleNamespace(_models=[model]), time.monotonic())
        self.assertEqual(self.bridge.image.read(40010, 4), data)
        self.assertEqual(self.bridge.image.write_addresses, {40012})

    def test_failed_source_does_not_redate_cached_measurements(self):
        self.bridge.source.last_update_success = False
        self.bridge.snapshot()
        self.assertIsNone(self.bridge.last_snapshot)
        self.assertFalse(self.bridge.image.registers)


class ConfigurationTests(unittest.TestCase):
    def test_existing_settings_override_safe_new_install_defaults(self):
        saved = {"allow_writes": True, "allowed_networks": "192.0.2.20/32"}
        bridge = SunSpecBridge(SimpleNamespace(), SimpleNamespace(data=saved, options={}))
        self.assertFalse(DEFAULTS["allow_writes"])
        self.assertEqual(DEFAULTS["allowed_networks"], "127.0.0.1/32")
        self.assertTrue(bridge.settings["allow_writes"])
        self.assertEqual(bridge.settings["allowed_networks"], saved["allowed_networks"])

    def test_invalid_or_empty_networks_are_rejected(self):
        for networks in ("", " , ", "192.0.2.20/24", "invalid"):
            with self.assertRaises(ValueError):
                validate({"listen_host": "0.0.0.0", "allowed_networks": networks})
        validate({"listen_host": "0.0.0.0", "allowed_networks": "192.0.2.20/32,127.0.0.1/32"})

    def test_overlapping_listener_is_rejected_but_own_options_can_change(self):
        entry = SimpleNamespace(
            entry_id="old", data={"listen_host": "0.0.0.0", "port": 502}, options={}
        )
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _: [entry]))
        self.assertTrue(listener_in_use(hass, {"listen_host": "192.0.2.10", "port": 502}))
        self.assertFalse(listener_in_use(hass, {"listen_host": "192.0.2.10", "port": 1502}))
        self.assertFalse(listener_in_use(hass, {"listen_host": "0.0.0.0", "port": 502}, "old"))


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_does_not_remove_the_already_consumed_once_listener(self):
        bridge = SunSpecBridge(SimpleNamespace(), SimpleNamespace(data={}, options={}))
        bridge.stop_unsubscribe = Mock(side_effect=AssertionError("listener already removed"))
        await bridge.stop(None)
        self.assertTrue(bridge.closed)
        self.assertIsNone(bridge.stop_unsubscribe)

    async def test_close_cancels_background_refresh_and_unsubscribes(self):
        bridge = SunSpecBridge(SimpleNamespace(), SimpleNamespace(data={}, options={}))
        removers = [Mock(), Mock(), Mock()]
        bridge.source_unsubscribe, bridge.timer_unsubscribe, bridge.stop_unsubscribe = removers
        bridge.maintenance_task = asyncio.create_task(asyncio.Event().wait())
        await asyncio.sleep(0)
        await bridge.close()
        self.assertTrue(bridge.maintenance_task.cancelled())
        for remove in removers:
            remove.assert_called_once()
        await bridge.close()  # Idempotent cleanup does not double-remove callbacks.
        for remove in removers:
            remove.assert_called_once()

    async def test_serialization_failure_never_escapes_source_callback(self):
        bridge = SunSpecBridge(SimpleNamespace(), SimpleNamespace(data={}, options={}))
        bridge.source = SimpleNamespace(
            last_update_success=True, api=SimpleNamespace(_last_model_read_end=1)
        )
        with patch.object(
            bridge, "prepare_layout", side_effect=RuntimeError("third-party encoder changed")
        ):
            with self.assertLogs("custom_components.fems_sunspec_bridge", level="ERROR"):
                bridge.snapshot()
        self.assertIsNone(bridge.last_snapshot)
        self.assertIn("third-party encoder changed", bridge.server.last_error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
