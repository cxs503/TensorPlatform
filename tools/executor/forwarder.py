#!/usr/bin/env python3
"""TCP forwarder: LAN-facing endpoint for the SeaweedFS S3 gateway.

weed binds 127.0.0.1 only; backend containers run under rootless docker
with --disable-host-loopback, so the slirp alias 10.0.2.2 cannot reach the
host loopback either. This forwards host LAN IP :8334 -> 127.0.0.1:8333
(binds the LAN IP only, never 0.0.0.0; S3 credentials still required).
"""
from __future__ import annotations

import asyncio
import os

LISTEN_HOST = os.environ.get("FORWARD_LISTEN_HOST", "192.0.2.10")
LISTEN_PORT = int(os.environ.get("FORWARD_LISTEN_PORT", "8334"))
TARGET_HOST = os.environ.get("FORWARD_TARGET_HOST", "127.0.0.1")
TARGET_PORT = int(os.environ.get("FORWARD_TARGET_PORT", "8333"))


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    try:
        upstream_r, upstream_w = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
    except Exception:
        client_w.close()
        return
    await asyncio.gather(_pipe(client_r, upstream_w), _pipe(upstream_r, client_w))


async def main() -> None:
    server = await asyncio.start_server(_handle, LISTEN_HOST, LISTEN_PORT)
    print(f"forwarding {LISTEN_HOST}:{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
