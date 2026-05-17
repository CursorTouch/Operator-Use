from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent   # tests/
ROOT = HERE.parent                        # project root

# Project root — so `import program` works
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# This directory — so `from helpers import ...` works
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
