"""Desktop app: a local API (``api.py``) and the window that shows it (``desktop.py``)."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(
    os.environ.get("SCHEMA_ENGINE_DATA")
    or Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "SchemaEngine"
)
"""Window size, logs, sample files and dropped files. Settings stay in ``config_path()``."""
