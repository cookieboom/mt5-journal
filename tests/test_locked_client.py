from __future__ import annotations

import threading
import time

from journal.adapter.base import MT5Client
from journal.adapter.fake import FakeMT5Client
from journal.ingest.locked_client import LockedMT5Client


def test_conforms_to_the_protocol():
    assert isinstance(LockedMT5Client(FakeMT5Client()), MT5Client)


def test_forwards_calls_to_the_inner_client():
    inner = FakeMT5Client()
    locked = LockedMT5Client(inner)
    assert locked.positions_get() == inner.positions_get()
    assert locked.account_info() == inner.account_info()


class _SlowSymbolInfoTick(FakeMT5Client):
    """symbol_info_tick sleeps briefly and records the [start, end] of its own
    call so the test can check for overlap across threads."""

    def __init__(self):
        super().__init__()
        self.spans: list[tuple[float, float]] = []
        self._spans_lock = threading.Lock()

    def symbol_info_tick(self, symbol):
        start = time.monotonic()
        time.sleep(0.05)
        end = time.monotonic()
        with self._spans_lock:
            self.spans.append((start, end))
        return super().symbol_info_tick(symbol)


def test_concurrent_calls_from_two_threads_never_overlap():
    inner = _SlowSymbolInfoTick()
    locked = LockedMT5Client(inner)

    def _call():
        locked.symbol_info_tick("XAUUSDc")

    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(inner.spans) == 2
    (s1, e1), (s2, e2) = inner.spans
    # One call's span must not overlap the other's — the lock serialized them.
    assert e1 <= s2 or e2 <= s1
