"""Request bodies the web accepts. FastAPI validates against these before a
handler runs, so a malformed body is a 422 with a readable `error` — never a
`TypeError` on `body["symbol"]` and a 500.

Settings blobs (chart/replay/risk/paper/trade-PNG prefs, drawings) stay
client-owned and free-form: the server only insists they are JSON objects.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class WatchRequest(BaseModel):
    symbol: str
    timeframe: str


class RangeRequest(BaseModel):
    symbol: str
    timeframe: str
    from_ms: int
    to_ms: int


class StorageFetchRequest(BaseModel):
    symbol: str = Field(min_length=1)
    # `tf` is the key the storage panel sent before it switched to `timeframe`.
    timeframe: str = Field("M1", validation_alias=AliasChoices("timeframe", "tf"))
    from_ms: int
    to_ms: int


class StorageFillGapsRequest(BaseModel):
    symbol: str = Field(min_length=1)
    timeframe: str = Field("M1", validation_alias=AliasChoices("timeframe", "tf"))


class StoragePruneRequest(BaseModel):
    symbol: str | None = None  # None or "all" → every symbol
    # A cutoff at or after now would make "older than" mean every candle.
    older_than_days: int = Field(180, ge=1)


class LabTrainRequest(BaseModel):
    """`lab_api.train` owns the optional knobs and their range checks; only the
    two keys it cannot default are required here."""
    model_config = ConfigDict(extra="allow")
    symbol: str
    timeframe: str


class IndicatorValidateRequest(BaseModel):
    source: str = Field(max_length=20_000)


class IndicatorScriptRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source: str = Field(max_length=20_000)


class BacktestExits(BaseModel):
    """The tester form's exit defaults (`domain/indicators/backtest.Settings`)."""
    sl: Literal["atr", "points", "none"] = "atr"
    sl_value: float = Field(default=1.5, gt=0)
    atr_len: int = Field(default=14, ge=1, le=5_000)
    tp_r: float | None = Field(default=2.0, gt=0)
    max_hold: int | None = Field(default=None, ge=1)
    opposite_closes: bool = True
    overlap: bool = False


class IndicatorBacktestRequest(BaseModel):
    """Exactly one of `script` (an id) or `source` (an unsaved draft)."""
    script: str | None = None
    source: str | None = Field(None, max_length=20_000)
    inputs: dict[str, float | bool] = Field(default_factory=dict)
    symbol: str = Field(min_length=1)
    timeframe: str
    from_ms: int
    to_ms: int
    split: float = Field(0.7, ge=0.05, le=0.95)
    exits: BacktestExits = Field(default_factory=BacktestExits)
    spread_fallback: float | None = Field(None, ge=0)


class IndicatorContextRequest(BaseModel):
    """Exactly one of `script` (an id) or `source` (an unsaved draft)."""
    script: str | None = None
    source: str | None = Field(None, max_length=20_000)
    inputs: dict[str, float | bool] = Field(default_factory=dict)
    symbol: str = Field(min_length=1)
    timeframe: str
    window: int = Field(default=3, ge=1, le=50)


class BacktestReplayRequest(BaseModel):
    symbol: str = Field(min_length=1)
    timeframe: str
    decision_msc: int
    exit_msc: int | None = None
    lead_bars: int = Field(default=50, ge=1, le=5_000)
    tail_bars: int = Field(default=20, ge=0, le=5_000)


class IndicatorComputeRequest(BaseModel):
    """Exactly one of `script` (an id) or `source` (an unsaved draft)."""
    script: str | None = None
    source: str | None = Field(None, max_length=20_000)
    inputs: dict[str, float | bool] = Field(default_factory=dict)
    symbol: str = Field(min_length=1)
    timeframe: str
    from_ms: int
    to_ms: int
    session_id: int | None = None
    include_forming: bool = False
