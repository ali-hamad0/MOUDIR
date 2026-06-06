"""Demand forecaster (Tasks 6.5/6.6). Per product, next-N-days demand (regression).

`train.py` builds the sklearn.Pipeline, compares >=3 candidates with TimeSeriesSplit,
logs to results.csv, and saves the winner to artifacts/. `predictor.py` wraps the
loaded pipeline behind the DemandPredictor seam (real | stub). Populated in Task 6.5+.
"""
