"""Put this directory on sys.path so tests can `from _harness import ...`."""

from __future__ import annotations

import sys
from pathlib import Path

_DOD = Path(__file__).resolve().parent
if str(_DOD) not in sys.path:
    sys.path.insert(0, str(_DOD))
