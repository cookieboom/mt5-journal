"""Walk-forward evaluation. Nothing here fits a model; it scores predictions
that already exist, which is why it can be tested with hand-written arrays.

Three things are non-negotiable in this file:
  * splits run forward in time and never shuffle. A random split leaks the
    future into the past and produces a beautiful, meaningless score.
  * a purge gap of `n_bars` sits between every train block and its test block,
    because each label already looked `n_bars` ahead.
  * every model number is reported beside a random-entry baseline over the same
    rows. An expectancy is only interesting relative to entering at random."""
from __future__ import annotations

import numpy as np

MIN_BUCKET_N = 20          # CLAUDE.md §8
_CALIBRATION_BUCKETS = 10


def purged_folds(n_rows: int, n_folds: int,
                 purge: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window folds with a `purge`-row gap before each test block.

    Returns [] when the data cannot support the split — a fold with an empty
    train or test side is worse than no answer."""
    if n_rows <= 0 or n_folds < 1:
        return []
    block = n_rows // (n_folds + 1)
    if block <= purge:
        return []

    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for i in range(1, n_folds + 1):
        train_end = i * block
        test_start = train_end + purge + 1
        test_end = min(test_start + block, n_rows)
        if test_start >= test_end:
            break
        folds.append((np.arange(0, train_end), np.arange(test_start, test_end)))
    return folds


def fold_metrics(y_true: np.ndarray, proba: np.ndarray, r_net: np.ndarray,
                 threshold: float) -> dict:
    """Scores one test block.

    `expectancy_r` covers only the rows the model would have taken (proba above
    the threshold); `baseline_expectancy_r` covers every row, which is what
    entering at random on this block would have returned."""
    taken = proba >= threshold
    n_taken = int(taken.sum())
    return {
        "n": int(len(y_true)),
        "n_taken": n_taken,
        "win_rate": float(y_true[taken].mean()) if n_taken else None,
        "expectancy_r": float(r_net[taken].mean()) if n_taken else None,
        "auc": _auc(y_true, proba),
        "baseline_expectancy_r": float(r_net.mean()) if len(r_net) else None,
        "calibration": _calibration(y_true, proba),
    }


def aggregate(folds: list[dict]) -> dict:
    """`n`-weighted mean across folds. A fold that took no entries contributes
    its `n` to the total but nothing to the averages it has no opinion on."""
    total_n = sum(f["n"] for f in folds)
    total_taken = sum(f["n_taken"] for f in folds)
    return {
        "n": total_n,
        "n_taken": total_taken,
        "win_rate": _weighted(folds, "win_rate", "n_taken"),
        "expectancy_r": _weighted(folds, "expectancy_r", "n_taken"),
        "auc": _weighted(folds, "auc", "n"),
        "baseline_expectancy_r": _weighted(folds, "baseline_expectancy_r", "n"),
        "calibration": _merge_calibration(folds),
        "folds": folds,
    }


def suppressed(value: float | None, n: int) -> float | None:
    """CLAUDE.md §8: a rate computed from fewer than 20 samples is noise with a
    decimal point. Callers render None as a dash, never as 0."""
    if value is None or n < MIN_BUCKET_N:
        return None
    return float(value)


def _auc(y_true: np.ndarray, proba: np.ndarray) -> float | None:
    """None when the block holds a single class — AUC is undefined there, and
    sklearn raises rather than returning it."""
    if len(np.unique(y_true)) < 2:
        return None
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y_true, proba))


def _calibration(y_true: np.ndarray, proba: np.ndarray) -> list[dict]:
    edges = np.linspace(0.0, 1.0, _CALIBRATION_BUCKETS + 1)
    out: list[dict] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (proba >= lo) & (proba < hi if hi < 1.0 else proba <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        out.append({
            "bucket": float((lo + hi) / 2),
            "predicted": float(proba[mask].mean()),
            "realised": float(y_true[mask].mean()),
            "n": n,
        })
    return out


def _weighted(folds: list[dict], key: str, weight_key: str) -> float | None:
    pairs = [(f[key], f[weight_key]) for f in folds
             if f.get(key) is not None and f.get(weight_key)]
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    return float(sum(v * w for v, w in pairs) / total) if total else None


def _merge_calibration(folds: list[dict]) -> list[dict]:
    merged: dict[float, dict] = {}
    for fold in folds:
        for bucket in fold.get("calibration", []):
            acc = merged.setdefault(
                bucket["bucket"],
                {"bucket": bucket["bucket"], "predicted": 0.0, "realised": 0.0, "n": 0},
            )
            acc["predicted"] += bucket["predicted"] * bucket["n"]
            acc["realised"] += bucket["realised"] * bucket["n"]
            acc["n"] += bucket["n"]
    out = []
    for acc in sorted(merged.values(), key=lambda a: a["bucket"]):
        n = acc["n"]
        out.append({
            "bucket": acc["bucket"],
            "predicted": acc["predicted"] / n,
            "realised": acc["realised"] / n,
            "n": n,
        })
    return out
