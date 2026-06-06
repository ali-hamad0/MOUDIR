"""`python -m app.ml.eval` → run the golden evals (Phase 6, Task 6.11)."""

import sys

from app.ml.eval.evaluate import main

if __name__ == "__main__":
    sys.exit(main())
