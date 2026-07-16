"""to_base: pure suffix lookup, suffix set = {"c"} only. See docs trap 12."""

from journal.domain.symbols import to_base


def test_strips_c_suffix():
    assert to_base("XAUUSDc") == "XAUUSD"
    assert to_base("EURUSDc") == "EURUSD"
    assert to_base("BTCUSDc") == "BTCUSD"


def test_usdcad_unchanged():
    # The canonical trap: USDCAD has no `c` suffix (ends in 'D'); it must not be
    # mangled. This is why the lookup is exact, not a guess.
    assert to_base("USDCAD") == "USDCAD"


def test_unsuffixed_symbol_returned_verbatim():
    assert to_base("XAUUSD") == "XAUUSD"


def test_min_base_len_guard():
    # Stripping 'c' here would leave a 2-char remainder; the guard keeps it whole.
    assert to_base("ABc") == "ABc"
    # 3-char remainder is allowed to strip.
    assert to_base("ABCc") == "ABC"


def test_other_broker_suffixes_not_stripped():
    # We deliberately do NOT strip .m/.raw/_ecn/#/-; they pass through verbatim.
    for sym in ("EURUSD.m", "EURUSD.raw", "EURUSD_ecn", "EURUSD#", "EURUSD-"):
        assert to_base(sym) == sym
