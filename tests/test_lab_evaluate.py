"""Evaluation. The purge gap is the whole reason this file exists: labels look
`n_bars` ahead, so a test block adjacent to its training block has already told
the model its own answer."""
from __future__ import annotations

import numpy as np
import pytest

from journal.lab.evaluate import (
    MIN_BUCKET_N,
    aggregate,
    fold_metrics,
    purged_folds,
    suppressed,
)


def test_folds_never_overlap_and_run_forward_in_time():
    folds = purged_folds(1_000, n_folds=5, purge=24)
    assert len(folds) == 5
    last_test_end = -1
    for train_idx, test_idx in folds:
        assert set(train_idx).isdisjoint(test_idx)
        assert test_idx.min() > last_test_end
        assert train_idx.max() < test_idx.min()
        last_test_end = test_idx.max()


def test_purge_gap_separates_train_from_test():
    purge = 24
    for train_idx, test_idx in purged_folds(1_000, n_folds=5, purge=purge):
        assert test_idx.min() - train_idx.max() > purge


def test_too_few_rows_yields_no_folds_rather_than_a_bad_split():
    assert purged_folds(50, n_folds=5, purge=24) == []


def test_expectancy_is_the_mean_net_r_of_taken_entries():
    y = np.array([1, 0, 1, 0])
    proba = np.array([0.9, 0.8, 0.7, 0.1])
    r_net = np.array([2.0, -1.0, 2.0, -1.0])
    m = fold_metrics(y, proba, r_net, threshold=0.5)
    # three entries pass the threshold: 2.0, -1.0, 2.0
    assert m["n_taken"] == 3
    assert m["expectancy_r"] == pytest.approx(1.0)
    assert m["win_rate"] == pytest.approx(2 / 3)


def test_baseline_uses_every_row_not_the_selected_ones():
    y = np.array([1, 0, 0, 0])
    proba = np.array([0.9, 0.1, 0.1, 0.1])
    r_net = np.array([2.0, -1.0, -1.0, -1.0])
    m = fold_metrics(y, proba, r_net, threshold=0.5)
    assert m["expectancy_r"] == pytest.approx(2.0)
    assert m["baseline_expectancy_r"] == pytest.approx(-0.25)


def test_auc_is_one_for_a_perfect_ranking_and_none_for_one_class():
    perfect = fold_metrics(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9]),
                           np.array([-1.0, -1.0, 2.0, 2.0]), threshold=0.5)
    assert perfect["auc"] == pytest.approx(1.0)

    one_class = fold_metrics(np.array([1, 1]), np.array([0.6, 0.7]),
                             np.array([2.0, 2.0]), threshold=0.5)
    assert one_class["auc"] is None


def test_calibration_buckets_carry_their_own_n():
    y = np.array([1] * 50 + [0] * 50)
    proba = np.array([0.9] * 50 + [0.1] * 50)
    m = fold_metrics(y, proba, np.where(y == 1, 2.0, -1.0), threshold=0.5)
    for bucket in m["calibration"]:
        assert set(bucket) == {"bucket", "predicted", "realised", "n"}
    assert sum(b["n"] for b in m["calibration"]) == 100


def test_aggregate_weights_folds_by_n():
    a = {"n": 100, "n_taken": 100, "win_rate": 0.6, "expectancy_r": 1.0,
         "auc": 0.7, "baseline_expectancy_r": 0.0, "calibration": []}
    b = {"n": 300, "n_taken": 300, "win_rate": 0.2, "expectancy_r": -1.0,
         "auc": 0.5, "baseline_expectancy_r": 0.0, "calibration": []}
    out = aggregate([a, b])
    assert out["n"] == 400
    assert out["expectancy_r"] == pytest.approx((100 * 1.0 + 300 * -1.0) / 400)
    assert len(out["folds"]) == 2


def test_thin_buckets_are_suppressed_per_section_8():
    assert suppressed(0.93, MIN_BUCKET_N - 1) is None
    assert suppressed(0.93, MIN_BUCKET_N) == pytest.approx(0.93)
