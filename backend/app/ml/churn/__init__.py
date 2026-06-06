"""Churn classifier (Task 6.8). Which customers are at risk (binary, imbalanced).

`train.py` compares LogisticRegression / RandomForest / XGBoost with class weighting,
k-fold CV, and reports PER-CLASS precision/recall/F1 (not macro-only — the imbalance
is real). `predictor.py` wraps the loaded pipeline behind the seam. Populated Task 6.8.
"""
