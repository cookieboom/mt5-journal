"""The two dependencies approved for the lab (CLAUDE.md rule 8). This test
exists so a fresh checkout fails loudly rather than at the first training run."""


def test_sklearn_importable():
    from sklearn.linear_model import LogisticRegression

    assert LogisticRegression is not None


def test_lightgbm_importable():
    import lightgbm as lgb

    assert hasattr(lgb, "LGBMClassifier")


def test_joblib_importable():
    import joblib

    assert hasattr(joblib, "dump") and hasattr(joblib, "load")
