"""Ingest: pull raw deals/orders/specs from an MT5Client into the store, and
verify the balance invariant. Nothing here imports MT5 — it depends only on the
`MT5Client` Protocol, so every path runs under `FakeMT5Client` (CLAUDE.md rule 1).
"""
