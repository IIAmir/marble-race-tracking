"""Minimal reference backend: accepts MarbleTrack connections and prints what arrives.

    python -m marbletrack_app.ws_receiver --port 8765 [--token SECRET]

Use it to try the app's streaming before the real backend exists, or as the
starting point for one. Messages are described in docs/WINDOWS_APP.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json


async def main(port: int, token: str, quiet: bool):
    from websockets.asyncio.server import serve
    from websockets.http11 import Response
    from websockets.datastructures import Headers

    def auth(connection, request):
        if token and request.headers.get("Authorization") != f"Bearer {token}":
            return Response(401, "Unauthorized", Headers([("Content-Length", "0")]), b"")
        return None

    async def handler(ws):
        peer = ws.remote_address
        print(f"connected: {peer}", flush=True)
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "leaderboard":
                if not quiet:
                    top = ", ".join(f"{e['rank']}.{e['name']}" + (f" {e['finish_time']:.3f}s" if e.get("finish_time") is not None else "")
                                    for e in msg["board"][:5])
                    print(f"[{msg['seq']}] {msg['race_state']:<8} t={msg['race_time']} {top}", flush=True)
            else:
                print(json.dumps(msg), flush=True)
        print(f"disconnected: {peer}", flush=True)

    async with serve(handler, "0.0.0.0", port, process_request=auth):
        print(f"listening on ws://0.0.0.0:{port}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default="")
    ap.add_argument("--quiet", action="store_true", help="print events only, not every leaderboard")
    a = ap.parse_args()
    asyncio.run(main(a.port, a.token, a.quiet))
