"""The web app answers to the local machine only.

`journal serve` refuses to BIND anywhere but loopback, but that is not enough:
the browser is what connects, and a DNS-rebinding page reaches 127.0.0.1 under
its own hostname — same-origin as far as the browser is concerned, so it could
read every response and POST to `/api/live/open`, which queues a real order.

So every request must name a loopback `Host`, and every state-changing request
that carries an `Origin` must come from a loopback page. A missing `Origin` is
allowed: curl and scripts send none, and browsers always send one cross-origin.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_loopback(host: str) -> bool:
    """True for a bare loopback host or address: `127.0.0.1`, `[::1]`, `localhost`."""
    return host.strip().strip("[]").lower() in LOOPBACK_HOSTS


def _hostname(netloc: str) -> str:
    """`127.0.0.1:8000` / `[::1]:8000` / `localhost` → the bare, lowercased host."""
    try:
        return urlsplit("//" + netloc).hostname or ""
    except ValueError:  # malformed, e.g. an unclosed '['
        return ""


async def guard(request: Request, call_next):
    if not is_loopback(_hostname(request.headers.get("host", ""))):
        return JSONResponse({"error": "Host bukan localhost — permintaan ditolak."},
                            status_code=403)
    origin = request.headers.get("origin")
    if (request.method not in _SAFE_METHODS and origin is not None
            and not is_loopback(_hostname(urlsplit(origin).netloc))):
        return JSONResponse({"error": "Origin bukan localhost — permintaan ditolak."},
                            status_code=403)
    return await call_next(request)
