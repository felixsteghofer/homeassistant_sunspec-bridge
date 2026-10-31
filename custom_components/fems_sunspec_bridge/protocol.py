"""Small, bounded Modbus TCP server over a timestamped register image."""

from __future__ import annotations

import asyncio
import ipaddress
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


class ModbusError(Exception):
    def __init__(self, code: int):
        self.code = code


@dataclass(frozen=True)
class Register:
    value: int
    updated: float | None  # None only for identity/layout, never measurements.


class RegisterImage:
    def __init__(self, max_age: float):
        self.max_age = max_age
        self.registers: dict[int, Register] = {}
        self.layout: tuple = ()
        self.write_addresses: set[int] = set()

    def put(self, address: int, data: bytes, updated: float | None) -> None:
        for offset, (value,) in enumerate(struct.iter_unpack(">H", data)):
            self.registers[address + offset] = Register(value, updated)

    def read(self, address: int, count: int) -> bytes:
        if not 1 <= count <= 125 or address + count > 65536:
            raise ModbusError(3)
        now = time.monotonic()
        result = []
        for index in range(address, address + count):
            register = self.registers.get(index)
            if register is None:
                raise ModbusError(2)
            if register.updated is not None and now - register.updated > self.max_age:
                raise ModbusError(11)
            result.append(register.value)
        return struct.pack(f">{count}H", *result)


class ModbusServer:
    def __init__(
        self,
        image: RegisterImage,
        unit: int,
        write: Callable[[int, bytes], Awaitable[None]],
        allowed_networks: str,
        changed: Callable[[], None] = lambda: None,
    ):
        self.image = image
        self.unit = unit
        self.write = write
        self.networks = [
            ipaddress.ip_network(item.strip())
            for item in allowed_networks.split(",")
            if item.strip()
        ]
        self.changed = changed
        self.server: asyncio.Server | None = None
        self.clients: set[asyncio.StreamWriter] = set()
        self.tasks: set[asyncio.Task] = set()
        self.reads = self.writes = self.errors = 0
        self.last_client: str | None = None
        self.last_error: str | None = None
        self.last_request: float | None = None

    async def start(self, host: str, port: int) -> None:
        self.server = await asyncio.start_server(self.handle_client, host, port, limit=1024)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
        for writer in list(self.clients):
            writer.close()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Python 3.14 waits for accepted clients too: close them before waiting.
        if self.server is not None:
            await self.server.wait_closed()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        address = peer[0] if peer else ""
        if len(self.clients) >= 8 or not any(
            ipaddress.ip_address(address) in n for n in self.networks
        ):
            writer.close()
            await writer.wait_closed()
            return
        task = asyncio.current_task()
        self.tasks.add(task)
        self.clients.add(writer)
        try:
            while True:
                async with asyncio.timeout(90):
                    header = await reader.readexactly(7)
                    transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                    if protocol != 0 or not 2 <= length <= 254:
                        return
                    pdu = await reader.readexactly(length - 1)
                self.last_client = address
                self.last_request = time.time()
                if unit != self.unit:
                    response = bytes((pdu[0] | 0x80, 11))
                    self.errors += 1
                    self.last_error = f"Unexpected unit {unit}"
                else:
                    response = await self.process(pdu)
                writer.write(
                    struct.pack(">HHHB", transaction, 0, len(response) + 1, unit) + response
                )
                await writer.drain()
                self.changed()
        except asyncio.IncompleteReadError, ConnectionError, TimeoutError:
            pass
        finally:
            self.clients.discard(writer)
            self.tasks.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def process(self, pdu: bytes) -> bytes:
        function = pdu[0]
        try:
            if function in (3, 4):
                if len(pdu) != 5:
                    raise ModbusError(3)
                address, count = struct.unpack(">HH", pdu[1:])
                data = self.image.read(address, count)
                self.reads += 1
                return bytes((function, len(data))) + data
            if function == 6:
                if len(pdu) != 5:
                    raise ModbusError(3)
                address = struct.unpack(">H", pdu[1:3])[0]
                data = pdu[3:5]
                response = pdu
            elif function == 16:
                if len(pdu) < 6:
                    raise ModbusError(3)
                address, count, byte_count = struct.unpack(">HHB", pdu[1:6])
                if not 1 <= count <= 123 or byte_count != count * 2 or len(pdu) != 6 + byte_count:
                    raise ModbusError(3)
                data = pdu[6:]
                response = bytes((16,)) + pdu[1:5]
            else:
                raise ModbusError(1)
            if not all(
                index in self.image.write_addresses
                for index in range(address, address + len(data) // 2)
            ):
                raise ModbusError(2)
            # Never acknowledge a control command until the inverter acknowledges it.
            async with asyncio.timeout(45):
                await self.write(address, data)
            self.writes += 1
            return response
        except ModbusError as error:
            code = error.code
            self.last_error = f"FC{function}: Modbus exception {code}"
        except Exception as error:
            code = 11
            self.last_error = f"FC{function}: {type(error).__name__}: {error}"
        self.errors += 1
        return bytes((function | 0x80, code))
