"""The ML layer (Phase 6). Real trained models — ML predicts, the LLM explains.

Constitution IV: numerical/statistical problems (demand forecasting, churn, revenue
anomaly) use trained scikit-learn / XGBoost models, never an LLM prompt. Training is
version-controlled code here (never only a notebook), retrainable from scratch with
one command (`python -m app.ml.train_all`); every experiment is logged to
`results.csv`; models load ONCE via the FastAPI lifespan and are served through DI.

Layout:
    features/      feature builders (as_of boundary, no leakage)         — Task 6.4
    demand/        demand-forecaster training + predictor                — Tasks 6.5/6.6
    churn/         churn-classifier training + predictor                 — Task 6.8
    anomaly/       revenue-anomaly detector training + predictor         — Task 6.9
    predictors.py  the prediction seam (real | stub), built once via DI  — Task 6.6+
    results.py     append-only experiment log helper (-> results.csv)    — this task
    artifacts/     trained *.joblib + model_card.json (committed if small)
    results.csv    the experiment log (header committed; rows appended by training)
"""
