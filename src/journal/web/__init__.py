"""M7 web dashboard — a read-mostly HTML layer over the existing analytics.

This package is a pure CONSUMER of the rest of the codebase: it calls the same
pure-DB functions the CLI does (`build_report`, `build_weekly`, `render_trade`,
`set_annotation`/`add_tag`/`remove_tag`) and renders their dataclasses as HTML.

It never touches the MT5 adapter (CLAUDE.md rules 1 & 12) — bridge operations
(`sync`, `candles`, `poll`, `rebuild`) stay in the CLI. The only writes it makes
are to the human layer (`annotations`, manual `tags`), keyed on `position_id`.
"""
