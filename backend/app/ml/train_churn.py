"""One-command entrypoint: `python -m app.ml.train_churn` (Phase 6, Task 6.8).

A thin shim so the retrain command is short and stable while the implementation lives in
app/ml/churn/train.py. `train_all` (Task 6.12) calls the same `train()` coroutine.
"""

from app.ml.churn.train import main

if __name__ == "__main__":
    main()
