"""The lab — the one predictive part of this tool (CLAUDE.md rule 9).

Trains candle-only models: a three-class regime classifier and a per-regime
triple-barrier entry-timing classifier. Nothing here imports MetaTrader5 or
FastAPI. Bars arrive from `store.candles_store.load_bars`; the only module that
touches sqlite is `lab.store`."""
