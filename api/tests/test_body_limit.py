"""BodySizeLimitMiddleware — caps request body at the ASGI layer, ignoring Content-Length
(so chunked/spoofed-length uploads can't bypass it). Driven directly via the ASGI protocol
so no server/event-loop-heavy harness is needed.

Run:  python api/tests/test_body_limit.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import types

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))   # import app.*

from app.security import BodySizeLimitMiddleware  # noqa: E402

CAP = 1000


async def _drive(method: str, chunks: list[bytes]):
    """Run the middleware against a fake downstream app. Returns (status, body_seen_by_app)."""
    seen = bytearray()

    async def downstream(scope, receive, send):
        # consume the whole body, then 200
        while True:
            msg = await receive()
            if msg["type"] == "http.request":
                seen.extend(msg.get("body", b""))
                if not msg.get("more_body", False):
                    break
            elif msg["type"] == "http.disconnect":
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = BodySizeLimitMiddleware(downstream, max_body=CAP)
    scope = {"type": "http", "method": method, "headers": []}

    queue = list(chunks)

    async def receive():
        if queue:
            body = queue.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(queue)}
        return {"type": "http.request", "body": b"", "more_body": False}

    status = {"code": None}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    await mw(scope, scope_receive := receive, send)  # noqa: F841
    return status["code"], bytes(seen)


def test_under_cap_passes():
    status, seen = asyncio.run(_drive("POST", [b"x" * 500]))
    assert status == 200, status
    assert seen == b"x" * 500          # full body forwarded to the app


def test_over_cap_in_one_chunk_rejected():
    status, seen = asyncio.run(_drive("POST", [b"x" * (CAP + 1)]))
    assert status == 413, status
    assert len(seen) <= CAP + 1        # app never received more than ~cap bytes


def test_over_cap_across_chunks_rejected():
    # split across many small chunks (mimics chunked Transfer-Encoding, no Content-Length)
    chunks = [b"x" * 100 for _ in range(20)]   # 2000 bytes total > CAP
    status, _ = asyncio.run(_drive("POST", chunks))
    assert status == 413, status


def test_exactly_cap_passes():
    status, seen = asyncio.run(_drive("POST", [b"x" * CAP]))
    assert status == 200, status
    assert len(seen) == CAP


def test_safe_method_not_buffered():
    # GET is skipped entirely (no body cap); the app still runs.
    status, _ = asyncio.run(_drive("GET", [b""]))
    assert status == 200, status


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and isinstance(o, types.FunctionType)]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok   {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
