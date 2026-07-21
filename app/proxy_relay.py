"""Локальный SOCKS5-релей без авторизации -> upstream SOCKS5 с авторизацией.

Зачем: Chromium (а значит Playwright/patchright) не умеет SOCKS5 с
логином/паролем. Поэтому поднимаем локальный SOCKS5 на 127.0.0.1 без авторизации,
а он форвардит соединения в upstream-прокси (с авторизацией) через python_socks.

Поддерживается только команда CONNECT (этого достаточно для браузера)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import struct

from python_socks import ProxyType
from python_socks.async_.asyncio import Proxy

logger = logging.getLogger(__name__)

_NO_AUTH = b"\x05\x00"
_CMD_CONNECT = 0x01


def _reply(rep: int) -> bytes:
    # VER, REP, RSV, ATYP=IPv4, BND.ADDR=0.0.0.0, BND.PORT=0
    return b"\x05" + bytes([rep]) + b"\x00\x01\x00\x00\x00\x00\x00\x00"


class Socks5Relay:
    """Поднимает локальный SOCKS5-сервер, форвардящий в upstream через python_socks."""

    def __init__(self, upstream_url: str, host: str = "127.0.0.1", port: int = 0) -> None:
        self.upstream_url = upstream_url
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None

    @property
    def address(self) -> str:
        """Адрес локального релея вида socks5://127.0.0.1:PORT."""
        return f"socks5://{self.host}:{self.port}"

    async def start(self) -> "Socks5Relay":
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        # если port=0, узнаём реально выбранный порт
        self.port = self._server.sockets[0].getsockname()[1]
        logger.info("SOCKS5-релей слушает %s -> upstream", self.address)
        return self

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> "Socks5Relay":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    # --- внутреннее -------------------------------------------------------
    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await self._negotiate_and_pipe(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as exc:  # noqa: BLE001 - релей не должен падать
            logger.debug("Ошибка в соединении релея: %s", exc)
        finally:
            writer.close()

    async def _negotiate_and_pipe(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # 1) Greeting: VER, NMETHODS, METHODS...
        ver, nmethods = struct.unpack("!BB", await reader.readexactly(2))
        await reader.readexactly(nmethods)
        if ver != 0x05:
            return
        writer.write(_NO_AUTH)  # выбираем "без авторизации"
        await writer.drain()

        # 2) Request: VER, CMD, RSV, ATYP, ADDR, PORT
        ver, cmd, _rsv, atyp = struct.unpack("!BBBB", await reader.readexactly(4))
        if atyp == 0x01:  # IPv4
            dest = socket.inet_ntoa(await reader.readexactly(4))
        elif atyp == 0x03:  # доменное имя
            length = (await reader.readexactly(1))[0]
            dest = (await reader.readexactly(length)).decode("utf-8", errors="ignore")
        elif atyp == 0x04:  # IPv6
            dest = str(ipaddress.ip_address(await reader.readexactly(16)))
        else:
            writer.write(_reply(0x08))  # address type not supported
            await writer.drain()
            return
        dest_port = struct.unpack("!H", await reader.readexactly(2))[0]

        if cmd != _CMD_CONNECT:
            writer.write(_reply(0x07))  # command not supported
            await writer.drain()
            return

        # 3) Открываем соединение к dest через upstream-прокси (с авторизацией)
        proxy = Proxy.from_url(self.upstream_url)
        try:
            sock = await proxy.connect(dest_host=dest, dest_port=dest_port)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Upstream connect failed (%s:%s): %s", dest, dest_port, exc)
            writer.write(_reply(0x05))  # connection refused
            await writer.drain()
            return

        writer.write(_reply(0x00))  # succeeded
        await writer.drain()

        # 4) Двунаправленный пайп между клиентом и upstream-сокетом
        remote_reader, remote_writer = await asyncio.open_connection(sock=sock)
        await self._pipe(reader, writer, remote_reader, remote_writer)

    @staticmethod
    async def _pipe(
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        remote_reader: asyncio.StreamReader,
        remote_writer: asyncio.StreamWriter,
    ) -> None:
        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                dst.close()

        await asyncio.gather(
            copy(client_reader, remote_writer),
            copy(remote_reader, client_writer),
        )
