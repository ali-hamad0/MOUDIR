"""One-command entrypoint: `python -m app.ml.train_anomaly` (Phase 6, Task 6.9).

A thin shim so the retrain command is short and stable while the implementation lives in
app/ml/anomaly/train.py. `train_all` (Task 6.12) calls the same `train()` coroutine.
"""

from app.ml.anomaly.train import main

if __name__ == "__main__":
    main()
