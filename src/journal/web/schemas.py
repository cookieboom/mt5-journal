"""Request bodies the web accepts. FastAPI validates against these before a
handler runs, so a malformed body is a 422 with a readable `error` — never a
`TypeError` on `body["symbol"]` and a 500.

Settings blobs (chart/replay/risk/paper/trade-PNG prefs, drawings) stay
client-owned and free-form: the server only insists they are JSON objects.
"""

from __future__ import annotations

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
