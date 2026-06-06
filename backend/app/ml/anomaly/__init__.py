"""Revenue anomaly detector (Task 6.9). Is today's revenue weird for this tenant?

Unsupervised + a tunable threshold: a seasonal-residual robust z-score / IQR baseline
compared against IsolationForest. `predictor.py` wraps it behind the seam. The model
must know Ramadan/summer are not anomalies (they are seasonality). Populated Task 6.9.
"""
