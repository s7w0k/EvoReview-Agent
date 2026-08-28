"""Execution entry so ``python -m evoagent.evaluation_v4`` (and the runtime CLI)
work as documented.  Defaults to the real-runtime runner (plan §4.1)."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())