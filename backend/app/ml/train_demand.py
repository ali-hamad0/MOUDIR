"""One-command entrypoint: `python -m app.ml.train_demand` (Phase 6, Task 6.5).

A thin shim so the retrain command is short and stable while the implementation lives in
app/ml/demand/train.py. `train_all` (Task 6.12) calls the same `train()` coroutine.
"""

from app.ml.demand.train import main

if __name__ == "__main__":
    main()
