"""Analytics: read-only reports over the `trades` table (M5).

Nothing here writes anything — `trades` is `domain/reconstruct.py`'s job, this
package only reads it. No MT5 client either (CLAUDE.md rule 1 is moot here,
but the shape matches `verify`/`rebuild`: pure DB in, a typed result out).

CLAUDE.md rule 9: this tool describes patterns in past data. Nothing in this
package may compute or suggest what to do next — only what already happened.
"""
